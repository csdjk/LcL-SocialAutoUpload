import copy
from PIL import Image
import pytest
from publishing.cover_assets import validate_cover_contract


def fixture(root):
    sizes = {'bilibili': (1200,900), 'landscape': (1440,1080), 'portrait': (1080,1440),
             'wechat_landscape': (1440,1080), 'wechat_portrait': (1080,1440),
             'youtube': (3840,2160), 'toutiao': (1920,1080), 'kuaishou': (1080,1440)}
    assets = {}
    for key, size in sizes.items():
        path = root / f'{key}.jpg'; Image.new('RGB', size, '#192b43').save(path)
        assets[key] = {'path':path.name, 'width':size[0], 'height':size[1], 'bytes':path.stat().st_size,
                       'max_bytes':2097152, 'format':'JPEG'}
    return {'cover_profile_version':2, 'assets':assets, 'platforms':{
        'bilibili':{'cover':'bilibili'}, 'douyin':{'cover_landscape':'landscape','cover_portrait':'portrait'},
        'wechat_channels':{'cover_landscape':'wechat_landscape','cover_portrait':'wechat_portrait'},
        'youtube':{'cover':'youtube'}, 'toutiao':{'cover':'toutiao'}, 'kuaishou':{'cover':'kuaishou'}}}


def test_actual_dimensions_and_platform_mapping(tmp_path):
    package=fixture(tmp_path)
    validate_cover_contract(package,tmp_path)
    wrong=copy.deepcopy(package);wrong['platforms']['youtube']['cover']='landscape'
    with pytest.raises(ValueError,match='比例'): validate_cover_contract(wrong,tmp_path)
    Image.new('RGB',(1920,1080)).save(tmp_path/'youtube.jpg')
    with pytest.raises(ValueError,match='元数据'): validate_cover_contract(package,tmp_path)


def test_missing_cover_and_budget_fail_closed(tmp_path):
    package=fixture(tmp_path)
    package['assets']['kuaishou']['max_bytes']=1
    with pytest.raises(ValueError,match='体积'): validate_cover_contract(package,tmp_path)
    del package['assets']['kuaishou']
    with pytest.raises(ValueError,match='映射'): validate_cover_contract(package,tmp_path)
    validate_cover_contract({'assets':{}},tmp_path)  # unchanged old-edition path
