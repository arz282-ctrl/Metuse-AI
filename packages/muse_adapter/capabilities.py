"""Meta Model API / Muse capability registry and model routing (explicitly excludes video gen)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from packages.shared.enums import MuseCapability
from packages.shared.errors import CapabilityNotSupportedError


@dataclass(frozen=True)
class MuseModelSpec:
    """Advertised capability and estimated unit pricing for a Muse model family."""

    provider: str
    model_id: str
    capabilities: frozenset[MuseCapability]
    input_cost_per_1k_usd: Decimal
    output_cost_per_1k_usd: Decimal
    privacy_preserving_default: bool = True
    enabled: bool = True


DEFAULT_MUSE_MODELS: tuple[MuseModelSpec, ...] = (
    MuseModelSpec(
        provider="meta_muse",
        model_id="muse-spark-reasoning",
        capabilities=frozenset(
            {
                MuseCapability.REASONING_TOOL_USE,
                MuseCapability.REASONING_STRUCTURED_OUTPUT,
            }
        ),
        input_cost_per_1k_usd=Decimal("0.001500"),
        output_cost_per_1k_usd=Decimal("0.004500"),
    ),
    MuseModelSpec(
        provider="meta_muse",
        model_id="muse-ember-structured",
        capabilities=frozenset({MuseCapability.REASONING_STRUCTURED_OUTPUT}),
        input_cost_per_1k_usd=Decimal("0.000500"),
        output_cost_per_1k_usd=Decimal("0.001500"),
    ),
    MuseModelSpec(
        provider="meta_muse",
        model_id="muse-image",
        capabilities=frozenset({MuseCapability.IMAGE_GENERATION, MuseCapability.IMAGE_EDIT}),
        input_cost_per_1k_usd=Decimal("0.010000"),
        output_cost_per_1k_usd=Decimal("0.030000"),
    ),
    MuseModelSpec(
        provider="meta_muse",
        model_id="muse-voice-transcribe",
        capabilities=frozenset({MuseCapability.AUDIO_TRANSCRIPTION}),
        input_cost_per_1k_usd=Decimal("0.000600"),
        output_cost_per_1k_usd=Decimal("0.000000"),
    ),
    MuseModelSpec(
        provider="meta_muse",
        model_id="sam-segmentation",
        capabilities=frozenset({MuseCapability.SEGMENTATION}),
        input_cost_per_1k_usd=Decimal("0.002000"),
        output_cost_per_1k_usd=Decimal("0.002000"),
    ),
)


class MuseCapabilityRegistry:
    """Capability-based selector for Meta Muse models; rejects unsupported capabilities."""

    def __init__(self, models: tuple[MuseModelSpec, ...] = DEFAULT_MUSE_MODELS) -> None:
        self._models = models

    def list_models(self) -> list[MuseModelSpec]:
        return [m for m in self._models if m.enabled]

    def resolve_capability(self, capability: MuseCapability | str) -> MuseModelSpec:
        """Select the lowest-cost enabled Muse model supporting the requested capability."""
        if isinstance(capability, str) and not isinstance(capability, MuseCapability):
            try:
                resolved = MuseCapability(capability.strip().lower())
            except ValueError as exc:
                raise CapabilityNotSupportedError(
                    f"Capability '{capability}' is not exposed by the Meta Muse adapter "
                    "(video generation must use the dedicated video gateway)",
                    details={"requested_capability": capability},
                ) from exc
        else:
            resolved = capability

        candidates = [m for m in self._models if m.enabled and resolved in m.capabilities]
        if not candidates:
            raise CapabilityNotSupportedError(
                f"No enabled Meta Muse model supports capability '{resolved.value}'",
                details={"requested_capability": resolved.value},
            )
        candidates.sort(key=lambda m: m.input_cost_per_1k_usd + m.output_cost_per_1k_usd)
        return candidates[0]

    @staticmethod
    def estimate_cost_usd(
        spec: MuseModelSpec,
        *,
        input_units: int,
        output_units: int,
    ) -> Decimal:
        """Estimate USD cost for accounting (never displayed as a provider invoice)."""
        in_cost = (Decimal(max(0, input_units)) / Decimal(1000)) * spec.input_cost_per_1k_usd
        out_cost = (Decimal(max(0, output_units)) / Decimal(1000)) * spec.output_cost_per_1k_usd
        return (in_cost + out_cost).quantize(Decimal("0.000001"))
