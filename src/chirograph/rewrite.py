"""Path rewrite rules — how the two-path model gets populated in practice.

Spec §3.2's motivating example is a flat GEO deposit
(``GSE000000_sampleA_barcodes.tsv.gz``) that should materialise as a tidy
per-sample tree (``sampleA/barcodes.tsv.gz``). Doing that by hand for a real
deposit is not viable, so the writer takes regex rules::

    --relpath-from-source '^GSE000000_([^_]+)_(.*)$=>\\1/\\2'

The walked path is one of the two paths; a rule derives the other. Which
direction depends on what the tree in front of you *is*, which is why there are
two flags rather than one ambiguous one:

``--relpath-from-source``
    The tree is the **deposit** layout (you downloaded it). Walked path becomes
    ``source_path``; the rule computes ``relpath``.

``--source-from-relpath``
    The tree is the **workspace** layout (you are about to deposit it). Walked
    path becomes ``relpath``; the rule computes ``source_path``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .errors import ChirographError
from .paths import validate_path

__all__ = ["RewriteRule", "apply_rules", "parse_rules"]

SEPARATOR = "=>"


@dataclass(frozen=True)
class RewriteRule:
    """One ``REGEX=>REPLACEMENT`` rule."""

    pattern: re.Pattern[str]
    replacement: str

    @classmethod
    def parse(cls, spec: str, *, separator: str = SEPARATOR) -> RewriteRule:
        """Parse ``REGEX=>REPLACEMENT``, splitting on the *first* separator.

        Splitting on the first occurrence means a replacement may contain the
        separator but a pattern may not. That asymmetry is the right way round:
        replacements are literal-ish text where ``=>`` is plausible, patterns are
        regexes where it is not.
        """
        if separator not in spec:
            raise ChirographError(
                f"rewrite rule {spec!r} has no {separator!r}; expected REGEX{separator}REPLACEMENT"
            )
        raw_pattern, replacement = spec.split(separator, 1)
        if not raw_pattern:
            raise ChirographError(f"rewrite rule {spec!r} has an empty pattern")
        try:
            compiled = re.compile(raw_pattern)
        except re.error as exc:
            raise ChirographError(f"rewrite rule {spec!r} has an invalid regex: {exc}") from exc
        return cls(compiled, replacement)

    def applies_to(self, path: str) -> bool:
        return self.pattern.search(path) is not None

    def apply(self, path: str) -> str:
        try:
            return self.pattern.sub(self.replacement, path)
        except re.error as exc:
            raise ChirographError(
                f"rewrite rule {self.pattern.pattern!r}=>{self.replacement!r} failed on "
                f"{path!r}: {exc}"
            ) from exc


def parse_rules(specs: Iterable[str] | None) -> tuple[RewriteRule, ...]:
    """Parse a sequence of rule specifications."""
    return tuple(RewriteRule.parse(spec) for spec in (specs or ()))


def apply_rules(rules: Sequence[RewriteRule], path: str, *, field: str = "relpath") -> str:
    """Apply the first matching rule to *path*.

    First-match-wins, not sequential application. Chained substitutions are
    almost never what someone writing two rules for two different filename
    conventions wants, and are very hard to reason about when rule 2 fires on
    rule 1's output.

    The result is validated as a manifest path, so a rule that produces
    ``../escape`` or an absolute path fails at write time rather than at some
    consumer's checkout.
    """
    for rule in rules:
        if rule.applies_to(path):
            rewritten = rule.apply(path)
            if not rewritten:
                raise ChirographError(
                    f"rewrite rule {rule.pattern.pattern!r}=>{rule.replacement!r} mapped "
                    f"{path!r} to the empty string"
                )
            return validate_path(rewritten, field=field)
    return path
