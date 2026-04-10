"""轻量记忆层 - 基于 ChromaDB + 多语言 Embedding 的本地向量记忆存储"""
import hashlib
import logging
import subprocess
import os
import json
from datetime import datetime

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

log = logging.getLogger("memory")

KIRO_TIMEOUT = int(os.environ.get("KIRO_TIMEOUT", "120"))
EMBEDDING_MODEL = os.environ.get(
    "EMBEDDING_MODEL",
    "/home/ubuntu/modelscope/paraphrase-multilingual-MiniLM-L12-v2",
)

SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "memory_settings.json")


class MemoryLayer:
    def __init__(self, db_path="./memory_db"):
        self.ef = SentenceTransformerEmbeddingFunction(model_name=EMBEDDING_MODEL)
        self.client = chromadb.PersistentClient(path=db_path)
        self.collection = self.client.get_or_create_collection(
            name="chat_memory",
            metadata={"hnsw:space": "cosine"},
            embedding_function=self.ef,
        )
        self._settings = self._load_settings()
        log.info(f"记忆层初始化完成，已有 {self.collection.count()} 条记忆")

    # ---- 用户设置持久化 ----
    def _load_settings(self) -> dict:
        try:
            with open(SETTINGS_PATH, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_settings(self):
        with open(SETTINGS_PATH, "w") as f:
            json.dump(self._settings, f, ensure_ascii=False, indent=2)

    def is_enabled(self, user_id: str) -> bool:
        return self._settings.get(user_id, {}).get("enabled", True)

    def set_enabled(self, user_id: str, enabled: bool):
        self._settings.setdefault(user_id, {})["enabled"] = enabled
        self._save_settings()

    def add(self, user_id: str, text: str):
        """存入一条记忆（自动去重）"""
        doc_id = hashlib.md5(f"{user_id}:{text}".encode()).hexdigest()
        self.collection.upsert(
            ids=[doc_id],
            documents=[text],
            metadatas=[{"user_id": user_id, "ts": datetime.now().isoformat()}],
        )

    def search(self, user_id: str, query: str, top_k: int = 5) -> list[str]:
        """检索与 query 相关的记忆"""
        if self.collection.count() == 0:
            return []
        results = self.collection.query(
            query_texts=[query],
            n_results=top_k,
            where={"user_id": user_id},
        )
        return results["documents"][0] if results["documents"] else []

    def extract_and_store(self, user_id: str, conversation: str):
        """用 kiro-cli 从对话中提取值得记住的信息"""
        prompt = (
            "从以下对话中提取值得长期记住的关键信息（用户偏好、事实、决策等）。\n"
            "每条一行，只输出信息，不要编号不要解释。如果没有值得记住的，只输出'无'。\n\n"
            f"对话：\n{conversation}"
        )
        try:
            result = subprocess.run(
                ["kiro-cli", "chat", "--no-interactive", "-a", "--wrap", "never", prompt],
                capture_output=True, text=True, timeout=KIRO_TIMEOUT,
                env={**os.environ, "NO_COLOR": "1"},
            )
            output = result.stdout.strip()
            if not output or "无" == output.strip():
                return
            for line in output.splitlines():
                line = line.strip().lstrip("-•· ")
                if line and line != "无":
                    self.add(user_id, line)
                    log.info(f"新记忆 [{user_id}]: {line}")
        except Exception as e:
            log.warning(f"记忆提取失败: {e}")

    def list_all(self, user_id: str) -> list[str]:
        """列出某用户的所有记忆（调试用）"""
        results = self.collection.get(where={"user_id": user_id})
        return results["documents"] if results["documents"] else []

    def count(self) -> int:
        return self.collection.count()

    def clear(self, user_id: str = None):
        """清除记忆（调试用）"""
        if user_id:
            results = self.collection.get(where={"user_id": user_id})
            if results["ids"]:
                self.collection.delete(ids=results["ids"])
        else:
            # 清空整个集合
            self.client.delete_collection("chat_memory")
            self.collection = self.client.get_or_create_collection(
                name="chat_memory",
                metadata={"hnsw:space": "cosine"},
                embedding_function=self.ef,
            )
