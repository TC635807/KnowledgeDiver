"""会话导入导出路由。

提供会话下载为 ZIP 和从 ZIP 上传导入功能。
"""

import json
import logging
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from backend.models.user import User
from backend.routes.auth import get_current_user
from backend.storage.sqlite_card_store import SqliteCardStore
from backend.storage.raw_store import RawPageStore
from backend.storage.frontmatter_utils import parse_frontmatter, generate_frontmatter
from backend.storage.session_store import SessionStore

router = APIRouter()

# 上传限制：单 ZIP 最大 50MB，解压后最大 200MB（防 ZIP 炸弹）
MAX_UPLOAD_SIZE = 50 * 1024 * 1024       # 50 MB per ZIP
MAX_EXTRACTED_SIZE = 200 * 1024 * 1024    # 200 MB total extracted (zip-bomb guard)


def get_session_store(current_user: User = Depends(get_current_user)) -> SessionStore:
    """获取当前用户的会话存储仓库。"""
    return SessionStore(username=current_user.username)


@router.get("/api/sessions/{session_id}/download")
async def download_session(
    session_id: str,
    store: SessionStore = Depends(get_session_store),
) -> FileResponse:
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    safe_name = re.sub(r'[\\/:*?"<>|]', '_', session.name)
    tmp_dir = tempfile.mkdtemp()
    export_dir = os.path.join(tmp_dir, safe_name)
    os.makedirs(export_dir, exist_ok=True)

    try:
        card_store = SqliteCardStore(username=store.username, session_id=session_id)
        cards = card_store.list_cards()

        for card in cards:
            frontmatter = generate_frontmatter(card)
            md_content = f"{frontmatter}\n\n# {card.title}\n\n{card.content}"
            md_path = os.path.join(export_dir, f"{card.id}.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(md_content)

        session_meta = session.model_dump(mode="json")
        session_meta["card_count"] = len(cards)
        with open(os.path.join(export_dir, "session.json"), "w", encoding="utf-8") as f:
            json.dump(session_meta, f, ensure_ascii=False, indent=2)

        # 导出原始网页内容 (raw_pages)
        raw_store = RawPageStore(username=store.username, session_id=session_id)
        raw_urls = raw_store.list_urls()
        if raw_urls:
            raw_pages = []
            for entry in raw_urls:
                page = raw_store.get(entry["url"])
                if page:
                    raw_pages.append(page)
            with open(os.path.join(export_dir, "raw_pages.json"), "w", encoding="utf-8") as f:
                json.dump(raw_pages, f, ensure_ascii=False, indent=2)
            logger.info("[SessionIO] Exported %d raw pages to raw_pages.json", len(raw_pages))
        raw_store.close()

        zip_path = os.path.join(tmp_dir, safe_name)
        shutil.make_archive(zip_path, "zip", export_dir)

        return FileResponse(
            zip_path + ".zip",
            media_type="application/zip",
            filename=f"{safe_name}.zip",
        )
    finally:
        pass


@router.post("/api/sessions/upload")
async def upload_session(
    file: UploadFile = File(...),
    store: SessionStore = Depends(get_session_store),
) -> dict:
    """上传 ZIP 压缩包导入会话。自动解析 frontmatter、去重 UUID、防路径穿越。"""
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="请上传 .zip 文件")

    tmp_dir = tempfile.mkdtemp()
    zip_path = os.path.join(tmp_dir, "upload.zip")

    try:
        # Read file and check size
        content = await file.read()
        if len(content) > MAX_UPLOAD_SIZE:
            raise HTTPException(status_code=400, detail="上传文件不能超过 50MB")

        with open(zip_path, "wb") as f:
            f.write(content)

        # Validate ZIP
        if not zipfile.is_zipfile(zip_path):
            raise HTTPException(status_code=400, detail="文件不是有效的 ZIP 压缩包")

        # Check total uncompressed size before extracting (zip-bomb guard)
        total_uncompressed = sum(
            info.file_size for info in zipfile.ZipFile(zip_path, "r").infolist()
        )
        if total_uncompressed > MAX_EXTRACTED_SIZE:
            raise HTTPException(status_code=400, detail="压缩包解压后大小超过限制 (200MB)")

        extract_dir = os.path.join(tmp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)

        # Extract with path traversal protection
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.namelist():
                member_path = os.path.realpath(os.path.join(extract_dir, member))
                if not member_path.startswith(os.path.realpath(extract_dir) + os.sep):
                    continue  # skip path traversal attempts
                if member.endswith("/"):
                    os.makedirs(member_path, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(member_path), exist_ok=True)
                    with zf.open(member) as src, open(member_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)

        # Find session.json and .md files
        session_json_path = None
        md_files = []

        for root, dirs, files in os.walk(extract_dir):
            for fname in files:
                fpath = os.path.join(root, fname)
                if fname == "session.json" and session_json_path is None:
                    session_json_path = fpath
                elif fname.endswith(".md"):
                    md_files.append(fpath)

        # Determine session name
        if session_json_path:
            with open(session_json_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            session_name = meta.get("name", file.filename.replace(".zip", ""))
        elif md_files:
            # Derive name from folder in ZIP
            rel = os.path.relpath(md_files[0], extract_dir)
            top_dir = rel.split(os.sep)[0]
            session_name = top_dir if top_dir else file.filename.replace(".zip", "")
        else:
            session_name = file.filename.replace(".zip", "")

        # Create new session
        new_session = store.create_session(session_name)
        new_session_id = new_session.id
        card_store = SqliteCardStore(username=store.username, session_id=new_session_id)

        # Check existing UUIDs in all user sessions
        existing_ids = set()
        for s in store.list_sessions():
            cs = SqliteCardStore(username=store.username, session_id=s.id)
            for c in cs.list_cards():
                existing_ids.add(c.id)

        imported_count = 0
        for md_path in md_files:
            try:
                with open(md_path, "r", encoding="utf-8") as f:
                    raw = f.read()
            except Exception as e:
                logger.warning("Import file read skipped: %s", e)
                continue

            # Parse frontmatter
            try:
                meta, body = parse_frontmatter(raw)
            except Exception as e:
                logger.warning("Import frontmatter parse skipped: %s", e)
                continue

            title = meta.get("title", os.path.basename(md_path).replace(".md", ""))
            original_id = meta.get("id", "")
            card_links = meta.get("links", [])
            card_backlinks = meta.get("backlinks", [])
            sources = meta.get("sources", [])
            confidence = meta.get("confidence", 1.0)
            tags = meta.get("tags", [])
            metadata = meta.get("metadata", {})
            if not isinstance(metadata, dict):
                metadata = {}

            # Resolve UUID conflict
            if original_id and original_id in existing_ids:
                original_id = str(uuid.uuid4())
            elif not original_id:
                original_id = str(uuid.uuid4())

            from datetime import datetime as dt
            from backend.models.card import Card

            now = dt.utcnow()
            new_id = str(uuid.uuid4()) if not original_id else original_id
            links = card_links if isinstance(card_links, list) else []
            backlinks = card_backlinks if isinstance(card_backlinks, list) else []

            content = body if body else ""
            leading_title = f"# {title}"
            if leading_title and content.lstrip().startswith(leading_title):
                lines = content.splitlines()
                if len(lines) >= 3 and lines[0].strip() == leading_title and lines[1].strip() == "":
                    content = "\n".join(lines[2:]).strip()

            card = Card(
                id=new_id,
                title=title,
                content=content,
                metadata=metadata,
                links=links,
                backlinks=backlinks,
                created_at=now,
                updated_at=now,
                sources=sources if isinstance(sources, list) else [],
                confidence=float(confidence) if confidence else 1.0,
                tags=tags if isinstance(tags, list) else [],
            )
            card_store.create_card(card)

            existing_ids.add(new_id)
            imported_count += 1

        # 导入原始网页内容 (raw_pages.json)
        raw_imported = 0
        raw_json_path = os.path.join(extract_dir, "raw_pages.json")
        if not os.path.exists(raw_json_path):
            # 可能在 session.json 同目录下
            if session_json_path:
                alt = os.path.join(os.path.dirname(session_json_path), "raw_pages.json")
                if os.path.exists(alt):
                    raw_json_path = alt
        if os.path.exists(raw_json_path):
            try:
                with open(raw_json_path, "r", encoding="utf-8") as f:
                    raw_pages = json.load(f)
                raw_store = RawPageStore(username=store.username, session_id=new_session_id)
                for page in raw_pages:
                    raw_store.save(
                        url=page.get("url", ""),
                        title=page.get("title", ""),
                        content=page.get("content", ""),
                        method=page.get("method", "imported"),
                        metadata=page.get("metadata"),
                    )
                    raw_imported += 1
                raw_store.close()
                logger.info("[SessionIO] Imported %d raw pages", raw_imported)
            except Exception as e:
                logger.warning("[SessionIO] raw_pages import failed: %s", e)

        return {
            "status": "imported",
            "session": new_session.model_dump(mode="json"),
            "card_count": imported_count,
            "raw_pages_count": raw_imported,
        }
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="ZIP 文件已损坏")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="导入失败，请检查文件格式")
    finally:
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception as e:
            logger.warning("Temp dir cleanup failed: %s", e)


def generate_frontmatter_raw(data: dict) -> str:
    """Generate YAML frontmatter string from card-like dict."""
    import yaml
    # Filter to known keys
    known = ["id", "title", "created_at", "updated_at", "links", "backlinks",
             "sources", "confidence", "tags", "metadata"]
    fm = {k: v for k, v in data.items() if k in known}
    return "---\n" + yaml.dump(fm, allow_unicode=True, default_flow_style=False) + "---"
