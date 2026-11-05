"""Export an interactive standalone depth surface from local canonical history."""
import argparse
from datetime import datetime
import json
from pathlib import Path

import plotly.graph_objects as go

from config.settings import ROOT,Settings
from src.app.depth_surface import depth_surface,figure_spec
from src.data_pipeline.dnse_history import DNSEHistory
from src.models.orderbook import LOCAL_TZ


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date',required=True)
    parser.add_argument('--symbol',default='ACB')
    parser.add_argument('--at',default='15:10')
    parser.add_argument('--side',choices=['bid','ask','both'],default='bid')
    parser.add_argument('--cumulative',action='store_true')
    args=parser.parse_args()
    at=datetime.fromisoformat(args.date+'T'+args.at+':59.999999').replace(tzinfo=LOCAL_TZ)
    store=DNSEHistory(Settings().dnse_database_path)
    rows=store.sampled_records(args.symbol,args.date,at)
    model=depth_surface(rows,args.symbol,args.date,at)
    spec=figure_spec(model,args.side,args.cumulative)
    figure=go.Figure(data=spec['data'],layout=spec['layout'])
    figure.update_layout(title=dict(text=f'{args.symbol} · {args.date} · Ba mức giá quan sát được'),margin=dict(l=0,r=0,t=60,b=0))
    folder=ROOT/'data/exports/charts';folder.mkdir(parents=True,exist_ok=True)
    target=folder/f'depth3d_{args.symbol}_{args.date}.html'
    figure.write_html(target,include_plotlyjs=True,full_html=True,
        config=dict(scrollZoom=True,displaylogo=False,responsive=True))
    (target.with_suffix('.json')).write_text(json.dumps(model),encoding='utf-8')
    print(json.dumps(dict(path=str(target),points=model['points'],gaps=model['gaps'],bytes=target.stat().st_size)))


if __name__=='__main__':
    main()
