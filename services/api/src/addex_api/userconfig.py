"""Per-user addon settings, carried in the install URL (`/{config}/manifest.json`).

Stremio keeps calling whatever URL the user installed, so settings travel with every
request and Addex needs no accounts. The segment is base64url JSON, written by the
configure page: {"have": [addon ids already installed], "p2p": false to hide P2P}.
"""

import base64
import binascii
import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class UserConfig:
    have: frozenset[int] = field(default_factory=frozenset)
    hide_p2p: bool = False

    def wants(self, addon_id: int, p2p: bool) -> bool:
        """Whether to suggest this addon to the user."""
        return addon_id not in self.have and not (self.hide_p2p and p2p)

    def encode(self) -> str:
        data = {"have": sorted(self.have), "p2p": not self.hide_p2p}
        raw = json.dumps(data, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @classmethod
    def decode(cls, segment: str) -> "UserConfig | None":
        """None if the segment isn't a config this server wrote."""
        try:
            raw = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
            data = json.loads(raw)
            have = frozenset(int(i) for i in data.get("have", []))
            hide_p2p = data.get("p2p", True) is False
        except (binascii.Error, ValueError, TypeError, AttributeError):
            return None
        return cls(have=have, hide_p2p=hide_p2p)


DEFAULT = UserConfig()
