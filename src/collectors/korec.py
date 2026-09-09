"""전기위원회(korec.go.kr) 게시판 수집기.

게시판 구조 (2026-09-02 조사):
  - 위원회 개최결과 : POST /notice/result/selectNoticeList.do   (bbsSe=2)
  - 공지사항        : POST /notice/selectNoticeList.do          (bbsSe=1)
  - 상세            : POST /notice[/result]/moveNoticeDetail.do (bbsSntncNo, bbsSe)
  - 첨부 다운로드   : POST /commonfile/fileDownLoad.do
                      ?attachingFilePath=<filePath>&attachingFileNm=<fileName>

주의: 루트 요청 시 Accept 헤더가 없으면 JSON(배너 API)이 반환된다.
      반드시 text/html Accept 를 보낼 것.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://www.korec.go.kr"
LIST_URL = {2: f"{BASE}/notice/result/selectNoticeList.do",
            1: f"{BASE}/notice/selectNoticeList.do"}
DETAIL_URL = {2: f"{BASE}/notice/result/moveNoticeDetail.do",
              1: f"{BASE}/notice/moveNoticeDetail.do"}
DOWNLOAD_URL = f"{BASE}/commonfile/fileDownLoad.do"

BBS_LABEL = {2: "위원회 개최결과", 1: "공지사항"}

DEFAULT_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "jeonnam-re-data-collector/0.1 (contact: jhseo@kchglobal.co.kr)"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')
ROUND_RE = re.compile(r"제\s*(\d{2,4})\s*차")


@dataclass
class Posting:
    """게시글 1건. 첨부파일은 목록 페이지에 노출된 대표 첨부 기준."""

    bbs_se: int
    bbs_label: str
    bbs_sntnc_no: int
    번호: str
    분류: str
    제목: str
    게시일: str
    조회수: str
    첨부파일명: str
    첨부경로: str
    게시글URL: str
    첨부파일URL: str
    회차: int | None


def parse_round(title: str) -> int | None:
    """'제327차 전기위원회 개최결과' -> 327. 회차 표기가 없으면 None."""
    m = ROUND_RE.search(title)
    return int(m.group(1)) if m else None


def _hidden_value(tr, name: str) -> str:
    """행 안의 hidden input 값을 꺼낸다. 없으면 빈 문자열."""
    tag = tr.find("input", attrs={"name": name})
    return tag.get("value", "") if tag else ""


class KorecCollector:
    def __init__(self, delay_sec: float = 1.5, timeout_sec: int = 30, max_retries: int = 3):
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self.delay_sec = delay_sec
        self.timeout_sec = timeout_sec
        self.max_retries = max_retries
        self._warmed = False

    def _sleep(self) -> None:
        time.sleep(self.delay_sec)

    def _request(self, method: str, url: str, **kw) -> requests.Response:
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.request(method, url, timeout=self.timeout_sec, **kw)
                response.raise_for_status()
                return response
            except Exception as exc:      # 문서별 실패를 기록하고 계속 진행 (지침 §19)
                last_error = exc
                time.sleep(self.delay_sec * attempt)
        raise RuntimeError(f"{method} {url} 실패 ({self.max_retries}회): {last_error}")

    def warmup(self, bbs_se: int = 2) -> None:
        """세션 쿠키 확보. 첨부 다운로드 전에 1회 필요하다."""
        if not self._warmed:
            self._request("GET", LIST_URL[bbs_se])
            self._warmed = True
            self._sleep()

    # ── 목록 ──────────────────────────────────────────────────────────
    def fetch_list_page(self, bbs_se: int, page_index: int) -> tuple[list[Posting], int]:
        """목록 1페이지를 파싱하여 (게시글 리스트, 전체건수)를 반환."""
        self.warmup(bbs_se)
        response = self._request(
            "POST", LIST_URL[bbs_se],
            data={"pageIndex": page_index, "bbsSe": bbs_se,
                  "reSearchYn": "N", "seCode": "", "seCn": ""})
        soup = BeautifulSoup(response.text, "lxml")

        m = re.search(r"전체\s*([\d,]+)\s*건", soup.get_text())
        total = int(m.group(1).replace(",", "")) if m else -1

        tbody = soup.find("tbody")
        postings: list[Posting] = []
        if not tbody:
            return postings, total

        for tr in tbody.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if len(cells) < 4:
                continue

            title_tag = tr.find("a", href="#moveNoticeDetail")
            if title_tag is None:
                continue
            title = title_tag.get("title") or title_tag.get_text(" ", strip=True)

            # 공지사항(bbsSe=1)은 '분류' 칼럼이 하나 더 있다
            if bbs_se == 1 and len(cells) >= 6:
                번호, 분류, 게시일, 조회수 = cells[0], cells[1], cells[3], cells[4]
            else:
                번호, 분류, 게시일, 조회수 = cells[0], "", cells[2], cells[3]

            sntnc_no = _hidden_value(tr, "bbsSntncNo")
            file_name = _hidden_value(tr, "fileName")
            file_path = _hidden_value(tr, "filePath")

            postings.append(Posting(
                bbs_se=bbs_se,
                bbs_label=BBS_LABEL[bbs_se],
                bbs_sntnc_no=int(sntnc_no) if sntnc_no else -1,
                번호=번호, 분류=분류, 제목=title, 게시일=게시일, 조회수=조회수,
                첨부파일명=file_name, 첨부경로=file_path,
                게시글URL=f"{DETAIL_URL[bbs_se]}?bbsSntncNo={sntnc_no}&bbsSe={bbs_se}",
                첨부파일URL=(f"{DOWNLOAD_URL}?attachingFilePath={file_path}"
                             f"&attachingFileNm={file_name}") if file_name else "",
                회차=parse_round(title),
            ))
        return postings, total

    def fetch_all_postings(self, bbs_se: int, max_pages: int = 100,
                           on_page=None) -> list[Posting]:
        """전 페이지를 최신→과거 순으로 수집. 같은 게시물번호는 한 번만 담는다."""
        collected: list[Posting] = []
        seen: set[int] = set()
        for page in range(1, max_pages + 1):
            postings, total = self.fetch_list_page(bbs_se, page)
            fresh = [p for p in postings if p.bbs_sntnc_no not in seen]
            for posting in fresh:
                seen.add(posting.bbs_sntnc_no)
            collected.extend(fresh)
            if on_page:
                on_page(page, len(collected), total)
            if not postings or (total > 0 and len(seen) >= total):
                break
            self._sleep()
        return collected

    # ── 첨부파일 ──────────────────────────────────────────────────────
    def download_attachment(self, posting: Posting, dest_dir: Path) -> dict | None:
        """첨부파일을 내려받아 메타데이터를 반환. 이미 받은 파일은 다시 받지 않는다."""
        if not posting.첨부파일명:
            return None
        self.warmup(posting.bbs_se)
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / INVALID_FILENAME_CHARS.sub("_", posting.첨부파일명)

        if path.exists():                      # 증분 수집 (지침 §19)
            return self._build_meta(posting, path, path.read_bytes(), redownloaded=False)

        response = self._request(
            "POST", DOWNLOAD_URL,
            params={"attachingFilePath": posting.첨부경로,
                    "attachingFileNm": posting.첨부파일명},
            headers={"Referer": LIST_URL[posting.bbs_se]})
        path.write_bytes(response.content)
        self._sleep()
        return self._build_meta(posting, path, response.content, redownloaded=True)

    @staticmethod
    def _build_meta(posting: Posting, path: Path, data: bytes, redownloaded: bool) -> dict:
        return {
            "문서ID": f"KOREC-{posting.bbs_se}-{posting.bbs_sntnc_no}",
            "문서유형": posting.bbs_label,
            "문서제목": posting.제목,
            "회차": posting.회차,
            "게시일": posting.게시일,
            "게시글URL": posting.게시글URL,
            "첨부파일URL": posting.첨부파일URL,
            "원본파일명": posting.첨부파일명,
            "저장경로": str(path),
            "파일크기": len(data),
            "SHA256": hashlib.sha256(data).hexdigest(),
            "확장자": path.suffix.lower(),
            "신규다운로드": redownloaded,
            "수집일시": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        }


def to_records(postings: list[Posting]) -> list[dict]:
    return [asdict(p) for p in postings]
