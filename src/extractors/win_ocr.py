"""Windows 내장 OCR(Windows.Media.Ocr)로 PDF 페이지 텍스트를 읽는다.

일부 전기위원회 회의록은 한글 본문이 줄 단위 PNG 띠(예: 2047x114)로 렌더링되어
있어 PDF 텍스트 추출로는 값이 잡히지 않는다. 이 경우에만 사용한다.

Tesseract 설치가 필요 없다. 이 PC의 Windows OCR에 한국어(ko)가 이미 등록되어 있고,
엔진은 최대 10000px 이미지를 받는다. 렌더 해상도가 높을수록 인식률이 좋다.

지침 §22에 따라 이 경로로 얻은 값은 확정 데이터로 올리지 않는다. 호출부에서
추출방식='OCR', 데이터상태='검수필요'로 표시해 원문 대조 대상으로 남긴다.
"""

from __future__ import annotations

import asyncio
import re

import pymupdf
from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import BitmapDecoder
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

RENDER_DPI = 250
TWO_UP_RATIO = 1.15          # 가로가 세로보다 이만큼 길면 2쪽 배치로 본다

# 관찰된 반복 오독. 값 자체를 바꾸는 교정은 하지 않고, 항목 표지만 되돌린다.
BULLET_FIXES = (
    (re.compile(r"^\s*0\s+(?=[가-힣A-Za-z])"), "ㅇ "),    # 'ㅇ' 를 '0' 으로 읽는 경우
    (re.compile(r"^\s*[oO]\s+(?=[가-힣])"), "ㅇ "),
)


def create_engine():
    engine = OcrEngine.try_create_from_language(Language("ko"))
    if engine is None:
        raise RuntimeError(
            "Windows OCR 한국어 엔진을 만들 수 없습니다. "
            "설정 > 시간 및 언어에서 한국어 기본 기능(광학 문자 인식)을 확인하십시오.")
    return engine


async def _recognize(png: bytes, engine):
    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream.get_output_stream_at(0))
    writer.write_bytes(png)
    await writer.store_async()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    return await engine.recognize_async(bitmap)


def normalize_line(text: str) -> str:
    """줄머리 기호 오독만 되돌린다. 본문 값은 손대지 않는다."""
    for pattern, replacement in BULLET_FIXES:
        text = pattern.sub(replacement, text, count=1)
    return text.rstrip()


async def _read_page(page, engine) -> list[tuple[str, int]]:
    """페이지에서 (줄, 구역번호)를 읽는다. 2쪽 배치면 좌·우를 따로 렌더링한다."""
    rect = page.rect
    two_up = rect.width > rect.height * TWO_UP_RATIO
    clips = ([pymupdf.Rect(0, 0, rect.width / 2, rect.height),
              pymupdf.Rect(rect.width / 2, 0, rect.width, rect.height)]
             if two_up else [rect])

    lines: list[tuple[str, int]] = []
    for column, clip in enumerate(clips, 1):
        pixmap = page.get_pixmap(dpi=RENDER_DPI, clip=clip)
        result = await _recognize(pixmap.tobytes("png"), engine)
        for line in result.lines:
            text = normalize_line(line.text)
            if text.strip():
                lines.append((text, column))
    return lines


async def _read_document(path: str, engine, page_limit: int | None):
    document = pymupdf.open(path)
    pages = document[:page_limit] if page_limit else document
    output: list[tuple[str, int, int]] = []
    for page_no, page in enumerate(pages, 1):
        for text, column in await _read_page(page, engine):
            output.append((text, (page_no, column), page_no))
    document.close()
    return output


def read_pdf_lines(path: str, page_limit: int | None = None) -> list[tuple[str, tuple, int]]:
    """PDF 전체를 OCR 하여 (줄, 구역키, 페이지) 목록을 돌려준다."""
    engine = create_engine()
    return asyncio.run(_read_document(path, engine, page_limit))


def read_pdf_text(path: str, page_limit: int | None = None) -> str:
    return "\n".join(text for text, _, _ in read_pdf_lines(path, page_limit))
