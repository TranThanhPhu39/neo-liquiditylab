from scripts import verify_monitor_hosted as hosted


def test_new_quote_day_without_trades_keeps_bubbles_on_actual_older_day(tmp_path,monkeypatch):
    monkeypatch.setattr(hosted,'ROOT',tmp_path)
    entries=[dict(symbol='ACB',day=f'2026-10-{day:02d}',dataset_id=f'old-{day}') for day in range(1,31)]
    entries += [dict(symbol='ACB',day=f'2026-11-{day:02d}',dataset_id=f'new-{day}') for day in range(1,5)]
    def request(path):
        if path=='/api/hosting':return dict(seed_ready=True)
        if path=='/api/datasets':return dict(datasets=entries)
        if path.startswith('/api/dataset?'):return dict(dataset_id='new-4',frames=[dict(bids=[1],asks=[1])])
        if path.startswith('/api/trades?'):return dict(symbol='ACB',trades=[dict(volume=100)] if path.endswith('new-2') else [])
        if path=='/api/library':return []
        if path=='/api/review-history':return dict(reviews=[])
        raise AssertionError(path)
    monkeypatch.setattr(hosted,'request',request)
    result=hosted.verify()
    assert result['replay_day']=='2026-11-04' and result['latest_day_trade_count']==0
    assert result['bubbles_day']=='2026-11-02' and result['real_trades_for_bubbles']==1
    assert (tmp_path/'report/monitor_hosted_verification.json').exists()


def test_recovering_host_does_not_report_data_verified(monkeypatch):
    monkeypatch.setattr(hosted,'request',lambda path:dict(seed_ready=False))
    assert hosted.verify()['state']=='WAITING_FOR_RECOVERY'
