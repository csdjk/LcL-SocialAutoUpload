"""Validate reviewed covers without rewriting immutable packages."""
from PIL import Image


def validate_cover_contract(package, root):
    version = package.get('cover_profile_version')
    if version is None:
        return  # Existing editions retain their original assets and hashes.
    if version != 2:
        raise ValueError('不支持的封面规格版本')
    roles = {
        'bilibili': {'cover': (4, 3)},
        'douyin': {'cover_landscape': (4, 3), 'cover_portrait': (3, 4)},
        'wechat_channels': {'cover_landscape': (4, 3), 'cover_portrait': (3, 4)},
        'youtube': {'cover': (16, 9)}, 'toutiao': {'cover': (16, 9)}, 'kuaishou': {'cover': (3, 4)},
    }
    for platform, fields in roles.items():
        material = package.get('platforms', {}).get(platform, {})
        for field, ratio in fields.items():
            asset = package['assets'].get(material.get(field))
            if not asset:
                raise ValueError(f'{platform} 缺少独立封面映射 {field}')
            width, height = asset.get('width'), asset.get('height')
            limit = asset.get('max_bytes')
            if (type(width) is not int or type(height) is not int or min(width, height) < 640
                    or width * ratio[1] != height * ratio[0]
                    or type(limit) is not int or limit <= 0 or asset['bytes'] > limit):
                raise ValueError(f'{platform} 封面尺寸、比例或体积不符合交付规格')
            with Image.open(root / asset['path']) as image:
                if image.size != (width, height) or image.format != 'JPEG' or asset.get('format') != 'JPEG':
                    raise ValueError(f'{platform} 封面实际图片与元数据不符')
                image.verify()
