from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import parse_qsl, urlsplit

from app.domain import ExternalIdentifierPolicy


VISIT_UUID_MACRO = "{{aio.visit.uuid}}"
_EXTERNAL_ID_RE = re.compile(r"\{external[_-]?id\}|\{externalid\}", re.IGNORECASE)
_AIO_MACRO_RE = re.compile(r"\{\{aio\.[a-zA-Z0-9_.]+\}\}")
_RAW_PLACEHOLDER_RE = re.compile(r"(?<!\{)\{[^{}\s]{1,128}\}(?!\})")
_EXACT_RAW_PLACEHOLDER_RE = re.compile(r"^\{[^{}\s]{1,128}\}$")
_URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
_WARNING_RE = re.compile(r"[a-z0-9_]{1,128}")
_PLACEHOLDER_REPLACEMENTS = (
    (
        re.compile(r"\{(?:click[_-]?id|visit[_-]?uuid|yourclickid)\}", re.IGNORECASE),
        VISIT_UUID_MACRO,
    ),
    (
        re.compile(r"\{(?:sub1|spaceforyoursubs|buyer[_-]?id)\}", re.IGNORECASE),
        "{{aio.visit.fields.buyer_utm}}",
    ),
    (
        re.compile(r"\{(?:creative|ad[_-]?name)\}", re.IGNORECASE),
        "{{aio.visit.fields.fb_ad_name}}",
    ),
    (
        re.compile(r"\{(?:adset|ad[_-]?set[_-]?name)\}", re.IGNORECASE),
        "{{aio.visit.fields.fb_ad_set_name}}",
    ),
)


class DestinationContractError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class DestinationResultItem:
    naming: str
    url: str
    warnings: tuple[str, ...] = ()


def parse_model_results(
    text: str,
    *,
    server_namings: tuple[str, ...] | None = None,
) -> tuple[DestinationResultItem, ...]:
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise DestinationContractError(
            "Destination model response is not valid JSON",
            code="invalid_model_json",
        ) from exc
    if not isinstance(payload, dict) or set(payload) != {"results"}:
        raise DestinationContractError(
            "Destination model response has an invalid top-level shape",
            code="invalid_model_shape",
        )
    rows = payload["results"]
    if not isinstance(rows, list) or not rows:
        raise DestinationContractError(
            "Destination model response has no results",
            code="invalid_model_shape",
        )
    if server_namings is not None and len(rows) != len(server_namings):
        raise DestinationContractError(
            "Destination result count does not match server naming count",
            code="invalid_model_shape",
        )
    results: list[DestinationResultItem] = []
    expected_keys = (
        {"destination_url", "warnings"}
        if server_namings is not None
        else {"naming", "destination_url", "warnings"}
    )
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != expected_keys:
            raise DestinationContractError(
                "Destination result has an invalid shape",
                code="invalid_model_shape",
            )
        naming = (
            server_namings[index]
            if server_namings is not None
            else row["naming"]
        )
        url = row["destination_url"]
        warnings = row["warnings"]
        if (
            not isinstance(naming, str)
            or not naming.strip()
            or len(naming) > 512
            or not isinstance(url, str)
            or not url.strip()
            or len(url) > 16_384
            or not isinstance(warnings, list)
            or any(
                not isinstance(warning, str)
                or _WARNING_RE.fullmatch(warning) is None
                for warning in warnings
            )
        ):
            raise DestinationContractError(
                "Destination result contains invalid values",
                code="invalid_model_shape",
            )
        results.append(
            DestinationResultItem(
                naming=naming.strip(),
                url=url.strip(),
                warnings=tuple(warnings),
            )
        )
    return tuple(results)


def extract_source_urls(text: str) -> tuple[str, ...]:
    return tuple(match.group(0).rstrip("),.;]") for match in _URL_RE.finditer(text))


def parse_rendered_results(text: str) -> tuple[DestinationResultItem, ...]:
    blocks = text.split("\n\n")
    if len(blocks) % 2:
        raise DestinationContractError(
            "Rendered Destination has an invalid block count",
            code="invalid_rendered_result",
        )
    results: list[DestinationResultItem] = []
    for index in range(0, len(blocks), 2):
        naming = blocks[index].strip()
        prefix = "Destination:\n"
        destination = blocks[index + 1]
        if not naming or not destination.startswith(prefix):
            raise DestinationContractError(
                "Rendered Destination has an invalid block",
                code="invalid_rendered_result",
            )
        results.append(DestinationResultItem(naming, destination[len(prefix):].strip()))
    if not results:
        raise DestinationContractError(
            "Rendered Destination has no result",
            code="invalid_rendered_result",
        )
    return tuple(results)


def render_results(results: Iterable[DestinationResultItem]) -> str:
    return "\n\n".join(
        f"{item.naming}\n\nDestination:\n{item.url}" for item in results
    )


def validate_destination_url(
    *,
    source_url: str,
    result_url: str,
    external_id_policy: tuple[ExternalIdentifierPolicy, ...] = (),
    advertiser_url_parameters: str = "",
) -> str:
    """Inspect a model result; never construct or repair a URL."""
    source = _validated_split(source_url.strip())
    result_url = result_url.strip()
    result = _validated_split(result_url)
    _validate_aio_macros(result_url)
    if _RAW_PLACEHOLDER_RE.search(result_url):
        raise DestinationContractError(
            "Destination URL contains an unresolved placeholder",
            code="unresolved_placeholder",
        )
    if _EXTERNAL_ID_RE.search(result_url):
        raise DestinationContractError(
            "Destination URL contains an unresolved external identifier",
            code="unresolved_external_id",
        )

    if (
        source.scheme,
        source.netloc,
        source.path,
        source.fragment,
    ) != (
        result.scheme,
        result.netloc,
        result.path,
        result.fragment,
    ):
        raise DestinationContractError(
            "Destination model changed the URL base",
            code="destination_base_changed",
        )

    source_pairs = parse_qsl(source.query, keep_blank_values=True)
    result_pairs = parse_qsl(result.query, keep_blank_values=True)
    owned_parameters = advertiser_owned_parameters(advertiser_url_parameters)
    policy_by_parameter = {
        policy.parameter.casefold(): policy for policy in external_id_policy
    }
    has_dynamic_advertiser_tail = _has_dynamic_advertiser_tail(
        advertiser_url_parameters
    )
    has_continuation_advertiser_tail = _has_continuation_advertiser_tail(
        advertiser_url_parameters
    )
    expected: list[tuple[str, set[str]]] = []
    for parameter, value in source_pairs:
        folded_parameter = parameter.casefold()
        if folded_parameter in owned_parameters:
            continue
        if _EXTERNAL_ID_RE.search(value):
            policy = policy_by_parameter.get(folded_parameter)
            if policy is None:
                raise DestinationContractError(
                    f"No external identifier policy for {parameter}",
                    code="missing_external_id_policy",
                )
            if policy.action == "remove":
                continue
            if policy.action != "replace_visit_uuid":
                raise DestinationContractError(
                    f"Unsupported external ID policy for {parameter}",
                    code="invalid_external_id_policy",
                )
            expected.append((policy.output_parameter or parameter, {VISIT_UUID_MACRO}))
            continue
        if _EXACT_RAW_PLACEHOLDER_RE.fullmatch(value):
            if has_continuation_advertiser_tail:
                allowed_values = _allowed_values(value)
                if len(allowed_values) == 1:
                    raise DestinationContractError(
                        f"No advertiser mapping for placeholder parameter {parameter}",
                        code="missing_placeholder_mapping",
                    )
                expected.append((parameter, allowed_values))
                continue
            if has_dynamic_advertiser_tail:
                continue
            raise DestinationContractError(
                f"No advertiser mapping for placeholder parameter {parameter}",
                code="missing_placeholder_mapping",
            )
        expected.append((parameter, _allowed_values(value)))

    if len(expected) != len(result_pairs):
        raise DestinationContractError(
            "Destination query parameter count changed unexpectedly",
            code="destination_query_changed",
        )
    for (expected_name, allowed_values), (result_name, result_value) in zip(
        expected, result_pairs, strict=True
    ):
        if expected_name != result_name or result_value not in allowed_values:
            raise DestinationContractError(
                "Destination query parameters changed unexpectedly",
                code="destination_query_changed",
            )
    return result_url


def advertiser_owned_parameters(url_parameters: str) -> frozenset[str]:
    clean = url_parameters.strip().lstrip("?&")
    if not clean:
        return frozenset()
    if clean.startswith(("_", "-", ".")):
        _, separator, clean = clean.partition("&")
        if not separator:
            return frozenset()
    return frozenset(
        name.casefold()
        for name, _ in parse_qsl(clean, keep_blank_values=True)
        if name
    )


def _has_dynamic_advertiser_tail(url_parameters: str) -> bool:
    clean = url_parameters.strip().lstrip("?&")
    return bool(clean and _AIO_MACRO_RE.search(clean))


def _has_continuation_advertiser_tail(url_parameters: str) -> bool:
    clean = url_parameters.strip().lstrip("?&")
    return clean.startswith(("_", "-", "."))


def contains_external_id(url: str) -> bool:
    return bool(_EXTERNAL_ID_RE.search(url))


def validated_url(url: str) -> str:
    _validated_split(url.strip())
    _validate_aio_macros(url)
    return url.strip()


def _validated_split(url: str):
    if not url or any(char.isspace() or ord(char) < 32 for char in url):
        raise DestinationContractError(
            "Destination URL contains whitespace or control characters",
            code="invalid_destination_url",
        )
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise DestinationContractError(
            "Destination URL must be an absolute HTTP(S) URL",
            code="invalid_destination_url",
        )
    if parsed.username is not None or parsed.password is not None:
        raise DestinationContractError(
            "Destination URL must not contain credentials",
            code="invalid_destination_url",
        )
    return parsed


def _validate_aio_macros(url: str) -> None:
    without_valid = _AIO_MACRO_RE.sub("", url)
    if "{{aio." in without_valid or "{aio." in without_valid:
        raise DestinationContractError(
            "Destination URL contains a malformed AIO macro",
            code="malformed_aio_macro",
        )


def _allowed_values(source_value: str) -> set[str]:
    allowed = {source_value}
    transformed = source_value
    changed = False
    for pattern, replacement in _PLACEHOLDER_REPLACEMENTS:
        transformed, count = pattern.subn(replacement, transformed)
        changed = changed or count > 0
    if changed:
        allowed.add(transformed)
    return allowed
