"""
配置加载器 - 支持热更新
监听 config.yaml 修改时间变化，自动 reload
"""
import yaml
import threading
import time
from pathlib import Path
from typing import Any, Dict

CONFIG_PATH = Path(__file__).parent / "config.yaml"


class ConfigLoader:
    _instance = None
    _lock = threading.Lock()
    _config: Dict[str, Any] = {}
    _mtime: float = 0

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance

    def _load(self):
        with self._lock:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                self._config = yaml.safe_load(f) or {}
            self._mtime = CONFIG_PATH.stat().st_mtime

    def reload_if_changed(self):
        """如果文件被修改则重新加载"""
        try:
            mtime = CONFIG_PATH.stat().st_mtime
            if mtime != self._mtime:
                self._load()
                return True
        except FileNotFoundError:
            pass
        return False

    def get(self, key_path: str, default=None):
        """支持点路径，如 'account.initial_cash'"""
        keys = key_path.split(".")
        val = self._config
        for k in keys:
            if isinstance(val, dict) and k in val:
                val = val[k]
            else:
                return default
        return val

    @property
    def all(self) -> Dict[str, Any]:
        self.reload_if_changed()
        return self._config


config = ConfigLoader()