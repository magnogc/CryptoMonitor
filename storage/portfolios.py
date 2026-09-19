"""Private GitHub persistence: one atomic, non-forced commit per transaction."""
import base64
import json
import re

import requests


class StorageError(RuntimeError):
    pass


class ConflictError(StorageError):
    pass


class GitHubPortfolioStore:
    def __init__(self, token, repository="magnogc/CryptoMonitorData", branch="main", session=None):
        if not token or not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
            raise StorageError("Configure o acesso ao repositÃ³rio privado nos Secrets.")
        self.base = f"https://api.github.com/repos/{repository}"
        self.branch = branch
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})

    def _api(self, method, path, **kwargs):
        try:
            r = self.session.request(method, self.base + path, timeout=30, **kwargs)
        except requests.RequestException:
            raise StorageError("NÃ£o foi possÃ­vel acessar o armazenamento privado.") from None
        if r.status_code in (409, 422):
            raise ConflictError("Os dados mudaram. Recarregue a carteira antes de salvar novamente.")
        if not r.ok:
            raise StorageError(f"Armazenamento indisponÃ­vel (HTTP {r.status_code}). Confira acesso e configuraÃ§Ã£o.")
        return r.json()

    def _head(self):
        if not self._api("GET", "").get("private"):
            raise StorageError("O repositÃ³rio de carteiras precisa ser privado.")
        return self._api("GET", f"/git/ref/heads/{self.branch}")["object"]["sha"]

    def _read(self, path, ref):
        data = self._api("GET", f"/contents/{path}", params={"ref": ref})
        return base64.b64decode(data["content"]).decode("utf-8")

    def list_portfolios(self):
        head = self._head()
        return json.loads(self._read("index.json", head))["portfolios"]

    def load(self, portfolio_id):
        self._validate_id(portfolio_id)
        head = self._head()
        p = json.loads(self._read(f"portfolios/{portfolio_id}.json", head))
        history = [json.loads(x) for x in self._read(f"history/{portfolio_id}.jsonl", head).splitlines() if x]
        return p, history

    @staticmethod
    def _validate_id(value):
        if not re.fullmatch(r"[0-9a-f]{32}", value):
            raise StorageError("ID de carteira invÃ¡lido.")

    def save(self, portfolio, event, expected_revision=None):
        pid = portfolio["id"]
        self._validate_id(pid)
        head = self._head()
        index = json.loads(self._read("index.json", head))
        exists = any(p["id"] == pid for p in index["portfolios"])
        history = ""
        if exists:
            old = json.loads(self._read(f"portfolios/{pid}.json", head))
            if old["revision"] != expected_revision:
                raise ConflictError("Carteira alterada em outra sessÃ£o. Recarregue antes de salvar.")
            history = self._read(f"history/{pid}.jsonl", head)
        elif expected_revision is not None:
            raise ConflictError("Carteira nÃ£o encontrada. Recarregue antes de salvar.")
        elif portfolio["revision"] != 1:
            raise StorageError("RevisÃ£o inicial invÃ¡lida.")
        if exists and portfolio["revision"] != expected_revision + 1:
            raise StorageError("SequÃªncia de revisÃµes invÃ¡lida.")
        if not exists:
            index["portfolios"].append({"id": pid, "name": portfolio["name"], "created_at": portfolio["created_at"]})
        contents = {"index.json": json.dumps(index, ensure_ascii=False, indent=2),
                    f"portfolios/{pid}.json": json.dumps(portfolio, ensure_ascii=False, indent=2, allow_nan=False),
                    f"history/{pid}.jsonl": history + json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n"}
        base_tree = self._api("GET", f"/git/commits/{head}")["tree"]["sha"]
        tree = self._api("POST", "/git/trees", json={"base_tree": base_tree, "tree": [
            {"path": path, "mode": "100644", "type": "blob", "content": content} for path, content in contents.items()]})
        commit = self._api("POST", "/git/commits", json={"message": f"Portfolio {pid}: revision {portfolio['revision']}", "tree": tree["sha"], "parents": [head]})
        # A concurrent write creates a sibling commit: non-fast-forward is rejected.
        self._api("PATCH", f"/git/refs/heads/{self.branch}", json={"sha": commit["sha"], "force": False})
