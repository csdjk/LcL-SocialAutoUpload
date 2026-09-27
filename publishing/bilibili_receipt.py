"""Only structured successful API responses count as a submission receipt."""
import json
import re


def parse_receipt(output):
    decoder = json.JSONDecoder()
    found = set()
    for match in re.finditer(r"\{", output):
        try:
            value, _ = decoder.raw_decode(output[match.start():])
        except ValueError:
            continue
        if not isinstance(value, dict) or type(value.get("code")) is not int or value["code"] != 0:
            continue
        data = value.get("data")
        if not isinstance(data, dict):
            continue
        bvid = data.get("bvid")
        if isinstance(bvid, str) and re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid) and str(data.get("aid", "")).isdigit():
            found.add(bvid)
    if len(found) != 1:
        return None
    return {"status": "found", "remote_id": found.pop(), "source": "biliup_api_receipt",
            "note": "biliup 返回结构化成功回执和稿件 ID，仍需平台审核；不等同于公开发布。"}
