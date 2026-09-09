"""HWP 5.0 / HWPX 본문 텍스트 추출기 (한컴오피스 불필요).

한컴 COM 자동화는 보안모듈(FilePathCheckDLL)이 등록되지 않으면 Open 단계에서
승인 대화상자를 띄우고 멈춘다. 이 환경에서 RegisterModule 이 False 를 반환하므로
COM 경로를 쓰지 않고 파일 포맷을 직접 읽는다.

  .hwp  : OLE 복합문서. BodyText/SectionN 스트림을 (필요시 zlib raw 해제 후)
          레코드 단위로 훑어 HWPTAG_PARA_TEXT 의 UTF-16LE 본문을 모은다.
  .hwpx : OWPML. ZIP 안 Contents/section*.xml 의 <hp:t> 텍스트를 모은다.

2단 편집 문서라도 본문 스트림은 문서 논리 순서대로 저장되어 있어,
PDF 로 변환해 읽을 때 생기는 좌우 단 혼입이 발생하지 않는다.
"""

from __future__ import annotations

import re
import struct
import zipfile
import zlib
from pathlib import Path

import olefile
from lxml import etree

HWPTAG_BEGIN = 0x10
HWPTAG_PARA_HEADER = HWPTAG_BEGIN + 50      # 66
HWPTAG_PARA_TEXT = HWPTAG_BEGIN + 51        # 67

# 본문에 섞이는 제어문자. 표/그림 등 개체 자리를 나타내며 텍스트가 아니다.
CONTROL_CHARS = set(range(0, 32)) - {0x0A, 0x0D}
# 인라인 제어문자(확장 제어문자는 16비트 8개를 더 차지한다)
EXTENDED_CONTROLS = {1, 2, 3, 11, 12, 14, 15, 16, 17, 18, 21, 22, 23}


def _iter_records(data: bytes):
    """HWP 레코드 스트림을 (tag_id, level, payload) 로 순회한다."""
    pos, size = 0, len(data)
    while pos + 4 <= size:
        (header,) = struct.unpack_from("<I", data, pos)
        pos += 4
        tag_id = header & 0x3FF
        level = (header >> 10) & 0x3FF
        length = (header >> 20) & 0xFFF
        if length == 0xFFF:
            if pos + 4 > size:
                break
            (length,) = struct.unpack_from("<I", data, pos)
            pos += 4
        payload = data[pos:pos + length]
        pos += length
        yield tag_id, level, payload


def _decode_para_text(payload: bytes) -> str:
    """PARA_TEXT 페이로드에서 실제 글자만 뽑는다. 제어문자 자리는 건너뛴다.

    글자는 UTF-16LE 코드유닛이라 서로게이트 쌍이 나올 수 있다. 코드유닛을 하나씩
    chr() 로 바꾸면 쌍이 깨지므로, 바이트로 모아 마지막에 한 번에 디코딩한다.
    """
    buffer = bytearray()
    i, n = 0, len(payload) - 1
    while i < n:
        (code,) = struct.unpack_from("<H", payload, i)
        if code in EXTENDED_CONTROLS:
            i += 16          # 확장 제어문자: 자신 포함 8개 WCHAR
            continue
        if code in CONTROL_CHARS:
            if code == 9:
                buffer += "\t".encode("utf-16-le")
            elif code in (10, 13):
                buffer += "\n".encode("utf-16-le")
            i += 2
            continue
        buffer += payload[i:i + 2]
        i += 2
    return buffer.decode("utf-16-le", errors="replace")


def extract_hwp(path: str | Path) -> str:
    """.hwp 본문 텍스트. 문단마다 줄바꿈으로 구분한다."""
    path = Path(path)
    if not olefile.isOleFile(str(path)):
        raise ValueError(f"OLE 형식이 아님(HWP 3.0 이하 가능성): {path.name}")

    ole = olefile.OleFileIO(str(path))
    try:
        header = ole.openstream("FileHeader").read()
        compressed = bool(header[36] & 0x01) if len(header) > 36 else False

        sections = sorted(
            ("/".join(entry) for entry in ole.listdir()
             if len(entry) == 2 and entry[0] == "BodyText" and entry[1].startswith("Section")),
            key=lambda s: int(re.search(r"(\d+)$", s).group(1)))

        paragraphs: list[str] = []
        for name in sections:
            raw = ole.openstream(name).read()
            data = zlib.decompress(raw, -15) if compressed else raw
            for tag_id, _level, payload in _iter_records(data):
                if tag_id == HWPTAG_PARA_TEXT:
                    text = _decode_para_text(payload)
                    if text.strip():
                        paragraphs.append(text)
        return "\n".join(paragraphs)
    finally:
        ole.close()


def extract_hwpx(path: str | Path) -> str:
    """.hwpx(OWPML) 본문 텍스트."""
    paragraphs: list[str] = []
    with zipfile.ZipFile(path) as zf:
        names = sorted(n for n in zf.namelist()
                       if re.match(r"Contents/section\d+\.xml$", n))
        for name in names:
            root = etree.fromstring(zf.read(name))
            for para in root.iter():
                if etree.QName(para).localname != "p":
                    continue
                pieces = [node.text for node in para.iter()
                          if etree.QName(node).localname == "t" and node.text]
                line = "".join(pieces).strip()
                if line:
                    paragraphs.append(line)
    return "\n".join(paragraphs)


def extract_text(path: str | Path) -> str:
    """확장자에 따라 알맞은 추출기를 부른다."""
    suffix = Path(path).suffix.lower()
    if suffix == ".hwp":
        return extract_hwp(path)
    if suffix == ".hwpx":
        return extract_hwpx(path)
    raise ValueError(f"지원하지 않는 확장자: {suffix}")
