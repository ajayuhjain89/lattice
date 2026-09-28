"""D-15: deployment trust classes in the egress decision (security-model §6, ADR-011).

Pure policy: what ``provider_decision`` allows for each trust class, and that no label,
claim or credential moves a provider under a more permissive rule. The database-level
proofs (payloads observed, other organizations, the schema) are in
``tests/retrieval/test_trust_classes.py`` and ``tests/ingestion/test_provider_eligibility.py``.
"""

from itertools import permutations

import pytest

from lattice.providers.egress import (
    DEPLOYMENTS,
    OrgEgress,
    ProviderDecision,
    ProviderRecord,
    provider_decision,
    trust_class,
)

LEVELS = ("internal", "confidential", "restricted")


def record(
    deployment: str,
    level: str = "internal",
    *,
    explicit: bool = False,
    training_opt_out: bool = True,
) -> ProviderRecord:
    return ProviderRecord("p", "m", deployment, level, training_opt_out, explicit)


def decide(org: OrgEgress, rec: ProviderRecord | None, deployment: str) -> ProviderDecision:
    return provider_decision(org, rec, deployment=deployment, training_opt_out=True)


@pytest.mark.parametrize(
    "value", [None, "", "private", "self_hosted", "LOCAL", "external", " local", "unknown", 1]
)
def test_anything_unrecognised_is_unknown_and_sends_nothing(value: object) -> None:
    assert trust_class(value) == "unknown"
    org = OrgEgress(external_egress_level="restricted", organization_controlled_level="restricted")
    # Declared unknown: refused before any record is consulted.
    assert decide(org, record("local", "restricted", explicit=True), str(value)) == (
        ProviderDecision(None, "deployment_unknown")
    )
    assert decide(org, None, str(value)) == ProviderDecision(None, "deployment_unknown")
    # Recorded unknown (a row written before D-15 or by hand): refused too.
    assert decide(org, record(str(value), "restricted", explicit=True), "external_api") == (
        ProviderDecision(None, "deployment_unknown")
    )


def test_the_known_classes() -> None:
    assert DEPLOYMENTS == ("local", "organization_controlled", "external_api")
    assert all(trust_class(d) == d for d in DEPLOYMENTS)


@pytest.mark.parametrize("deployment", DEPLOYMENTS)
def test_an_unrecorded_provider_is_never_approved(deployment: str) -> None:
    org = OrgEgress(external_egress_level="restricted", organization_controlled_level="restricted")
    assert decide(org, None, deployment) == ProviderDecision(None, "provider_unapproved")


# --- local -----------------------------------------------------------------------------


@pytest.mark.parametrize("external", LEVELS)
def test_local_is_bounded_by_its_own_approval_only(external: str) -> None:
    org = OrgEgress(external_egress_level=external)
    assert decide(org, record("local"), "local").level == "internal"  # the default
    for level in LEVELS:
        assert decide(org, record("local", level, explicit=True), "local").level == level


def test_local_still_obeys_data_use_terms() -> None:
    # A local model is declared training_opt_out; a record saying otherwise is obeyed.
    rec = record("local", "restricted", explicit=True, training_opt_out=False)
    assert decide(OrgEgress(data_origin="customer"), rec, "local") == ProviderDecision(
        None, "provider_data_use"
    )


# --- organization_controlled ----------------------------------------------------------


def test_organization_controlled_is_not_allowed_until_the_organization_sets_a_ceiling() -> None:
    rec = record("organization_controlled", "restricted", explicit=True)
    # Even with every other setting at its most permissive.
    org = OrgEgress(external_egress_level="restricted", data_origin="synthetic")
    assert decide(org, rec, "organization_controlled") == ProviderDecision(
        None, "deployment_not_allowed"
    )


@pytest.mark.parametrize("ceiling", LEVELS)
def test_organization_controlled_needs_an_explicit_approval(ceiling: str) -> None:
    org = OrgEgress(organization_controlled_level=ceiling)
    # Recorded on first use with the default approval: that is not an approval here.
    for level in LEVELS:
        assert decide(org, record("organization_controlled", level), "organization_controlled") == (
            ProviderDecision(None, "provider_unapproved")
        )


@pytest.mark.parametrize("ceiling", LEVELS)
@pytest.mark.parametrize("approved", LEVELS)
def test_organization_controlled_is_the_minimum_of_ceiling_and_approval(
    ceiling: str, approved: str
) -> None:
    org = OrgEgress(external_egress_level="internal", organization_controlled_level=ceiling)
    rec = record("organization_controlled", approved, explicit=True)
    level = decide(org, rec, "organization_controlled").level
    assert level == min(ceiling, approved, key=LEVELS.index)
    # Never implicitly restricted: restricted needs both the ceiling and the approval.
    assert (level == "restricted") == (ceiling == approved == "restricted")


def test_organization_controlled_obeys_data_use_terms() -> None:
    org = OrgEgress(organization_controlled_level="restricted", data_origin="customer")
    rec = record("organization_controlled", "restricted", explicit=True, training_opt_out=False)
    assert decide(org, rec, "organization_controlled") == ProviderDecision(
        None, "provider_data_use"
    )


def test_the_external_level_does_not_govern_organization_controlled() -> None:
    rec = record("organization_controlled", "confidential", explicit=True)
    for external in LEVELS:
        org = OrgEgress(external_egress_level=external, organization_controlled_level="internal")
        assert decide(org, rec, "organization_controlled").level == "internal"


# --- external_api ----------------------------------------------------------------------


@pytest.mark.parametrize("external", LEVELS)
@pytest.mark.parametrize("approved", LEVELS)
def test_external_api_is_the_minimum_of_external_level_and_approval(
    external: str, approved: str
) -> None:
    for explicit in (False, True):
        org = OrgEgress(external_egress_level=external, organization_controlled_level="restricted")
        rec = record("external_api", approved, explicit=explicit)
        assert decide(org, rec, "external_api").level == min(external, approved, key=LEVELS.index)


def test_external_api_data_use_is_enforced() -> None:
    rec = record("external_api", "restricted", explicit=True, training_opt_out=False)
    org = OrgEgress(external_egress_level="restricted", data_origin="customer")
    assert decide(org, rec, "external_api") == ProviderDecision(None, "provider_data_use")
    assert decide(OrgEgress(data_origin="synthetic"), rec, "external_api").level == "internal"
    # The implementation's own declaration counts too: either side saying "may use
    # submitted content" is enough to refuse.
    rec = record("external_api", "restricted", explicit=True)
    assert provider_decision(
        org, rec, deployment="external_api", training_opt_out=False
    ) == ProviderDecision(None, "provider_data_use")


# --- no class can be reached by relabelling ---------------------------------------------


@pytest.mark.parametrize(("recorded", "declared"), list(permutations(DEPLOYMENTS, 2)))
def test_a_declared_class_that_differs_from_the_record_sends_nothing(
    recorded: str, declared: str
) -> None:
    org = OrgEgress(external_egress_level="restricted", organization_controlled_level="restricted")
    rec = record(recorded, "restricted", explicit=True)
    assert decide(org, rec, declared) == ProviderDecision(None, "provider_deployment_mismatch")


def test_relabelling_an_external_record_as_organization_controlled_gains_nothing() -> None:
    # An external provider approved for restricted, capped by the external level.
    org = OrgEgress(external_egress_level="internal")
    assert decide(org, record("external_api", "restricted"), "external_api").level == "internal"
    # Its row relabelled (and the implementation made to claim the same): no ceiling
    # for the class and no explicit approval, so nothing at all is sent.
    relabelled = record("organization_controlled", "restricted")
    assert decide(org, relabelled, "organization_controlled").level is None
    # And a relabel to local without the implementation running locally is a mismatch.
    assert decide(org, record("local", "restricted"), "external_api") == ProviderDecision(
        None, "provider_deployment_mismatch"
    )
