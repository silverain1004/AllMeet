"""Confluence Cloud REST API — 페이지/폴더 CRUD.

인증: `api/_auth/confluence_basic.get_confluence_auth()` (Basic, Authorization 헤더).
구 프로젝트 confluence/client.py 포팅 — requests + Authorization 헤더 방식으로 변환.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import requests

from api._auth.confluence_basic import get_confluence_auth

logger = logging.getLogger(__name__)


class ConfluenceClient:
    """Confluence Cloud REST API 클라이언트."""

    def __init__(self, user_email: str = ""):
        auth = get_confluence_auth(user_email)
        self._base_url = auth["base_url"].rstrip("/")
        self._auth_header = auth["auth_header"]
        self._api_url = f"{self._base_url}/wiki/rest/api/content"
        self._api_v2_url = f"{self._base_url}/wiki/api/v2"

    def _headers(self, *, content_type: bool = False) -> dict:
        h: dict[str, str] = {
            "Authorization": self._auth_header,
            "Accept": "application/json",
        }
        if content_type:
            h["Content-Type"] = "application/json"
        return h

    # -------------------------------------------------------------------------
    # 페이지 (REST v1)
    # -------------------------------------------------------------------------

    def get_page(self, page_id: str, expand: Optional[str] = None) -> dict:
        """페이지 단건 조회. expand 예: 'body.storage', 'version,history'."""
        url = f"{self._api_url}/{page_id}"
        params: dict[str, str] = {}
        if expand:
            params["expand"] = expand
        res = requests.get(url, params=params or None, headers=self._headers())
        if res.status_code != 200:
            raise RuntimeError(f"페이지 조회 실패: {res.status_code} — {res.text[:300]}")
        return res.json()

    def get_page_storage(self, page_id: str) -> str:
        """페이지 body.storage HTML 반환."""
        page = self.get_page(page_id, expand="body.storage")
        return ((page.get("body") or {}).get("storage") or {}).get("value") or ""

    def list_page_versions(self, page_id: str, limit: int = 50) -> list[dict]:
        """페이지 버전이력. 각 항목: {number, when, by: {accountId, displayName}}"""
        url = f"{self._api_url}/{page_id}/version"
        res = requests.get(url, params={"limit": limit}, headers=self._headers())
        if res.status_code != 200:
            raise RuntimeError(f"버전이력 조회 실패: {res.status_code} — {res.text[:300]}")
        return res.json().get("results", [])

    def get_child_pages(self, parent_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """부모 페이지의 자식 페이지 목록 (v1 child API)."""
        url = f"{self._api_url}/{parent_id}/child/page"
        res = requests.get(url, params={"limit": limit}, headers=self._headers())
        if res.status_code != 200:
            raise RuntimeError(f"자식 목록 조회 실패: {res.status_code} — {res.text[:300]}")
        return res.json().get("results", [])

    def create_page(
        self,
        title: str,
        html_content: str,
        parent_id: str,
        space_key: str,
    ) -> dict:
        """Confluence 페이지 생성."""
        payload = {
            "type": "page",
            "title": title,
            "ancestors": [{"id": parent_id}],
            "space": {"key": space_key},
            "body": {"storage": {"value": html_content, "representation": "storage"}},
        }
        res = requests.post(
            self._api_url,
            data=json.dumps(payload),
            headers=self._headers(content_type=True),
        )
        if res.status_code != 200:
            raise RuntimeError(f"페이지 생성 실패: {res.status_code} — {res.text[:500]}")
        return res.json()

    def update_page(
        self,
        page_id: str,
        title: Optional[str] = None,
        html_content: Optional[str] = None,
    ) -> dict:
        """기존 페이지 제목/본문 업데이트."""
        page = self.get_page(page_id, expand="body.storage,version,ancestors,space")
        cur_title = page.get("title") or ""
        cur_html = ((page.get("body") or {}).get("storage") or {}).get("value") or ""
        version_num = int((page.get("version") or {}).get("number", 1)) + 1
        space_key = (page.get("space") or {}).get("key") or ""
        ancestors = page.get("ancestors") or []

        payload: dict[str, Any] = {
            "id": page_id,
            "type": "page",
            "title": title or cur_title,
            "version": {"number": version_num},
            "space": {"key": space_key},
            "body": {
                "storage": {
                    "value": html_content if html_content is not None else cur_html,
                    "representation": "storage",
                }
            },
        }
        if ancestors:
            payload["ancestors"] = [{"id": ancestors[-1].get("id")}]

        res = requests.put(
            f"{self._api_url}/{page_id}",
            data=json.dumps(payload),
            headers=self._headers(content_type=True),
        )
        if res.status_code not in (200, 202):
            raise RuntimeError(f"페이지 업데이트 실패: {res.status_code} — {res.text[:500]}")
        return res.json()

    # -------------------------------------------------------------------------
    # 폴더 (REST v2)
    # -------------------------------------------------------------------------

    def get_folder(self, folder_id: str) -> dict:
        """v2: 폴더 단건 조회 (spaceId 포함)."""
        url = f"{self._api_v2_url}/folders/{folder_id}"
        res = requests.get(url, headers=self._headers())
        if res.status_code != 200:
            raise RuntimeError(f"폴더 조회 실패: {res.status_code} — {res.text[:300]}")
        return res.json()

    def get_folder_direct_children(
        self, folder_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """v2: 폴더의 직접 자식 목록. 각 항목: id, title, type(folder|page)."""
        url = f"{self._api_v2_url}/folders/{folder_id}/direct-children"
        res = requests.get(url, params={"limit": limit}, headers=self._headers())
        if res.status_code != 200:
            raise RuntimeError(f"폴더 자식 목록 조회 실패: {res.status_code} — {res.text[:300]}")
        return res.json().get("results", [])

    def create_folder(
        self,
        space_id: str,
        title: str,
        parent_id: str,
    ) -> dict:
        """v2: 폴더 생성."""
        url = f"{self._api_v2_url}/folders"
        res = requests.post(
            url,
            json={"spaceId": space_id, "title": title, "parentId": parent_id},
            headers=self._headers(content_type=True),
        )
        if res.status_code != 200:
            raise RuntimeError(f"폴더 생성 실패: {res.status_code} — {res.text[:300]}")
        return res.json()

    # -------------------------------------------------------------------------
    # 페이지 접근 제한 (REST v1)
    # -------------------------------------------------------------------------

    def set_page_restrictions(
        self,
        page_id: str,
        account_id: str = "",
        allow_email: str = "",
    ) -> bool:
        """페이지를 지정 사용자만 볼 수 있게 제한. accountId 우선, 없으면 username(email)."""
        if not (account_id or allow_email):
            return False
        if account_id:
            user_spec = {"type": "known", "accountId": account_id}
        else:
            user_spec = {"type": "known", "username": allow_email}
        payload = [
            {"operation": "read", "restrictions": {"user": [user_spec]}},
            {"operation": "update", "restrictions": {"user": [user_spec]}},
        ]
        try:
            res = requests.put(
                f"{self._api_url}/{page_id}/restriction",
                json=payload,
                headers=self._headers(content_type=True),
            )
            return res.status_code in (200, 204)
        except Exception as e:
            logger.warning("페이지 접근 제한 설정 실패: %s", e)
            return False
