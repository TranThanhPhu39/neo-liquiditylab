"""Lossless Parquet storage for derived replay frames; JSON remains an HTTP format."""
import hashlib
import json
from pathlib import Path

from src.data_pipeline.parquet_history import checked_folder, confined


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def write_pack(root, package):
    import pyarrow as pa
    import pyarrow.parquet as pq
    root = Path(root).resolve()
    checksum = identity(package)
    folder = root/'data/compact/monitor'/(package['dataset_id']+'-'+checksum[:12]+'-v2')
    if folder.exists():
        checked_folder(root, dict(folder=folder.relative_to(root).as_posix()))
    else:
        folder.mkdir(parents=True)
        pq.write_table(pa.table({'frame_json': pa.array([encode(f) for f in package['frames']], type=pa.binary())}),
                       folder/'frames.parquet', compression='zstd', compression_level=6, row_group_size=8192)
        patches=package.get('event_patches')
        if patches is not None:
            pq.write_table(pa.table({'patch_json':pa.array([encode(p) for p in patches],type=pa.binary())}),
                folder/'event-patches.parquet',compression='zstd',compression_level=6,row_group_size=8192)
        (folder/'metadata.json').write_bytes(encode({k:v for k,v in package.items() if k not in
            (('frames','event_patches') if patches is not None else ('frames',))}))
        from src.data_pipeline.parquet_history import digest
        files=[dict(path=p.name,bytes=p.stat().st_size,sha256=digest(p)) for p in sorted(folder.iterdir())]
        manifest=dict(format='liquiditylab-parquet-replay-v2', dataset_id=package['dataset_id'],
                      event_patches_in_parquet=patches is not None,event_patch_rows=len(patches) if patches is not None else 0,
                      frames=len(package['frames']), semantic_sha256=checksum,files=files)
        (folder/'manifest.json').write_bytes(encode(manifest))
    relative=folder.relative_to(root).as_posix()
    restored=read_pack(root,relative)
    if restored!=package:raise ValueError('PARQUET_REPLAY_ROUNDTRIP_MISMATCH')
    return relative


def read_pack(root, relative):
    import pyarrow.parquet as pq
    folder=confined(root,relative)
    manifest=json.loads((folder/'manifest.json').read_bytes())
    if ((folder/'metadata.json').stat().st_size>20_000_000 or (folder/'frames.parquet').stat().st_size>80_000_000
            or pq.ParquetFile(folder/'frames.parquet').metadata.num_rows>50000):
        raise ValueError('PARQUET_REPLAY_LIMIT_EXCEEDED')
    package=json.loads((folder/'metadata.json').read_bytes())
    if manifest.get('event_patches_in_parquet'):
        patch_path=folder/'event-patches.parquet'
        if (patch_path.stat().st_size>80_000_000 or
            pq.ParquetFile(patch_path).metadata.num_rows>200000):
            raise ValueError('PARQUET_REPLAY_LIMIT_EXCEEDED')
        package['event_patches']=[json.loads(r['patch_json']) for r in pq.read_table(patch_path,columns=['patch_json']).to_pylist()]
        if len(package['event_patches'])!=manifest['event_patch_rows']:raise ValueError('PARQUET_REPLAY_IDENTITY_MISMATCH')
    package['frames']=[json.loads(r['frame_json']) for r in pq.read_table(folder/'frames.parquet',columns=['frame_json']).to_pylist()]
    if (package['dataset_id']!=manifest['dataset_id'] or len(package['frames'])!=manifest['frames']
            or identity(package)!=manifest['semantic_sha256']):
        raise ValueError('PARQUET_REPLAY_IDENTITY_MISMATCH')
    return package


class ReplayDocument:
    """Compatibility with callers that consume a JSON document's bytes/text."""
    def __init__(self, root, relative):
        self.root,self.relative=root,relative

    def read_bytes(self):
        return encode(read_pack(self.root,self.relative))+b'\n'

    def read_text(self, encoding='utf-8'):
        return self.read_bytes().decode(encoding)
