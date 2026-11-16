"""Render shared inventory as native menu rows without granting actions."""
from pathlib import Path

def clean(value):
    return "".join(
        c if ord(c) >= 32 and c != chr(127) else " "
        for c in str(value)
    )[:240]

def rows(report):
    system = report.get("system")
    kind = report.get("content_type")
    if system not in ("ps3", "ps4") or kind not in ("update", "dlc"):
        raise ValueError("Unsupported native inventory scope")
    candidates = report.get("rows")
    if not isinstance(candidates, list) or len(candidates) > 100:
        raise ValueError("Native menu candidate limit exceeded")
    result, seen = [], set()
    for row in candidates:
        if (
            row.get("application_id") != report.get("application_id")
            or row.get("content_type") != kind
            or row.get("installation_authorized") is not False
        ):
            raise ValueError("Native candidate identity or authorization mismatch")
        source = row.get("path")
        token = row.get("token")
        if (
            not isinstance(source, str) or not Path(source).is_absolute()
            or token != source.encode("utf-8").hex() or token in seen
        ):
            raise ValueError("Invalid or duplicate native candidate token")
        seen.add(token)

        section = row.get("section")
        verification = row.get("verification_state")
        if section == "AVAILABLE" and verification == "VERIFIED_EXTERNAL":
            state = "VERIFIED"
        elif section == "DETECTED" and verification == "UNVERIFIED":
            state = "UNVERIFIED"
        else:
            raise ValueError("Unsupported native verification state")

        installed = row.get("installed_status")
        if installed == "GAME_BOUND_INSTALLED_FILES_VERIFIED":
            proof = row.get("installed_evidence")
            if (
                system != "ps3" or kind != "update"
                or not isinstance(proof, dict)
                or proof.get("status") != installed
                or proof.get("application_id") != row["application_id"]
                or proof.get("game_relationship_verified") is not True
                or proof.get("source_bound_installed_files_verified") is not True
                or proof.get("installation_authorized") is not False
                or proof.get("in_game_availability") != "UNVERIFIED"
            ):
                raise ValueError("Invalid installed-file evidence")
            section, state = "AVAILABLE", "FILES_VERIFIED"
        elif installed not in ("NOT_ASSESSED", "UNRESOLVED"):
            raise ValueError("Unsupported installed evidence state")

        version = (
            row.get("installed_version") if state == "FILES_VERIFIED"
            else row.get("version")
        )
        label = ("V" + str(version)) if version else Path(source).stem
        result.append("\t".join((
            "ROW", section, state, clean(label), token
        )))
    return result

def summary(update_report, dlc_report):
    if (
        update_report.get("content_type") != "update"
        or dlc_report.get("content_type") != "dlc"
        or update_report.get("system") != dlc_report.get("system")
        or update_report.get("application_id") != dlc_report.get("application_id")
    ):
        raise ValueError("Summary inventory scopes disagree")
    labels = []
    for report in (update_report, dlc_report):
        rendered = rows(report)
        available = sum(line.split("\t")[1] == "AVAILABLE" for line in rendered)
        detected = len(rendered) - available
        labels.append(f"{available} AVAILABLE / {detected} DETECTED")
    return "SUMMARY\t" + "\t".join(labels)
