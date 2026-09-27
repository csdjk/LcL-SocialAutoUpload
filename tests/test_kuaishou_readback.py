from publishing.browser_readback import kuaishou_records


def row(**extra):
    return {'publishId': 3648679488, 'workId': None,
            'title': '本期研究。\n内容由AI生成\n#AI日报 #人工智能 #游戏AI',
            'uploadTime': 1790496689235, 'photoOwner': True,
            'publishStatus': 2, 'judgementStatus': 0, 'photoStatus': 0, **extra}


def records(*rows, remote_id=None):
    return kuaishou_records({'result': 1, 'data': {'list': list(rows)}},
                            '本期研究。\n#AI日报 #人工智能 #游戏AI', '2026-09-27', remote_id)


def test_pending_receipt_never_becomes_fake_work_id():
    result = records(row())[0]
    assert result['state'] == 'processing'
    assert result['remote_id'] is None
    assert result['publish_id'] == '3648679488'
    assert result['match_key'] == 'publish:3648679488'
    assert records(row(publishId=0)) == []


def test_published_work_uses_real_work_id_and_explicit_status():
    data = row(publishId=0, workId='3xazek4kcdt53u9', publishStatus=4, judgementStatus=1)
    result = records(data)[0]
    assert result['remote_id'] == '3xazek4kcdt53u9'
    assert result['state'] == 'published'
    for change in ({'photoStatus': 1}, {'judgementStatus': None}, {'publishStatus': 2}):
        assert records({**data, **change})[0]['state'] == 'processing'


def test_unrelated_rows_never_match_and_pending_receipts_remain_distinct():
    assert records(row(title='别的视频')) == []
    assert records(row(photoOwner=False)) == []
    assert records(row(uploadTime=0)) == []
    assert records(row(workId='3xazek4kcdt53u9'), remote_id='other-work') == []
    matches = records(row(), row(publishId=3648679489))
    assert len({r['match_key'] for r in matches}) == 2
