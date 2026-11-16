
"""Render Citron disabled-add-on changes into a temporary copy only."""
from pathlib import Path
import hashlib
import shutil
import tempfile
from PyQt5.QtCore import QSettings

def settings(path):
    result = QSettings(str(path), QSettings.IniFormat)
    result.setFallbacksEnabled(False)
    result.setIniCodec("UTF-8")
    return result

def values(config):
    return {key: config.value(key) for key in config.allKeys()}

def render_disabled(config_path, title_id, disabled):
    config_path = Path(config_path)
    if type(title_id) is not int or not 0 < title_id < 2**64:
        raise ValueError("Invalid game title ID")
    if (
        type(disabled) is not list or len(disabled) > 2048
        or any(
            type(item) is not str or not item or len(item) > 256
            or "\0" in item for item in disabled
        )
        or len(set(disabled)) != len(disabled)
    ):
        raise ValueError("Invalid disabled keys")

    original_bytes = config_path.read_bytes()
    original_hash = hashlib.sha256(original_bytes).hexdigest()

    with tempfile.TemporaryDirectory(prefix="citron-config-preview-") as tmp:
        copy = Path(tmp) / "qt-config.ini"
        copy.write_bytes(original_bytes)
        config = settings(copy)
        before = values(config)
        if config.status() != QSettings.NoError:
            raise ValueError("Configuration cannot be parsed")

        count = config.beginReadArray("DisabledAddOns")
        if not 0 <= count <= 4096:
            raise ValueError("Invalid game array size")
        seen = set()
        target = None
        for index in range(count):
            config.setArrayIndex(index)
            game = int(str(config.value("title_id")), 10)
            if not 0 < game < 2**64 or game in seen:
                raise ValueError("Invalid or duplicate game entry")
            seen.add(game)
            if game == title_id:
                target = index
        config.endArray()

        adding = target is None
        if adding:
            target = count
            if count >= 4096:
                raise ValueError("Game array limit reached")

        prefix = f"DisabledAddOns/{target + 1}/"
        config.beginWriteArray("DisabledAddOns", count + int(adding))
        config.setArrayIndex(target)
        if adding:
            config.setValue("title_id", str(title_id))
            config.setValue("title_id/default", False)
        config.remove("disabled")
        config.beginWriteArray("disabled", len(disabled))
        for index, item in enumerate(disabled):
            config.setArrayIndex(index)
            config.setValue("d", item)
            config.setValue("d/default", False)
        config.endArray()
        config.endArray()
        config.sync()
        if config.status() != QSettings.NoError:
            raise RuntimeError("Temporary configuration write failed")

        del config
        config = settings(copy)
        after = values(config)

        def unrelated(snapshot):
            return {
                key: value for key, value in snapshot.items()
                if not key.startswith(prefix + "disabled/")
                and key != "DisabledAddOns/size"
                and not (adding and key.startswith(prefix))
            }

        if unrelated(before) != unrelated(after):
            raise RuntimeError("Unrelated configuration changed")

        config.beginReadArray("DisabledAddOns")
        config.setArrayIndex(target)
        size = config.beginReadArray("disabled")
        actual = []
        for index in range(size):
            config.setArrayIndex(index)
            actual.append(config.value("d"))
        config.endArray()
        config.endArray()
        if actual != disabled:
            raise RuntimeError("Disabled keys failed round-trip")

        rendered = copy.read_bytes()
        del config

    if hashlib.sha256(config_path.read_bytes()).hexdigest() != original_hash:
        raise RuntimeError("Original configuration changed during preview")
    return original_hash, rendered
