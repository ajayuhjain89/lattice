# Onboarding fixture (synthetic)

A tiny, fully synthetic customer -- "Wrenfield Analytics" -- used by the P12.7 onboarding
verification: `make prod-onboard` (deploy/verification/onboard_check.py), the
reference-stack browser journey (apps/web/e2e/onboarding-stack.spec.ts) and the backend
journey test (backend/tests/onboarding). Nothing here is seeded: the organization is created
by `lattice org create`, and each export below is zipped and **uploaded** by its
administrator in the product, as a customer would.

| Folder | Source type | What it proves |
| ------ | ----------- | -------------- |
| `documents/` | Documents (Markdown export) | internal pages everyone reads; a `confidential` page the default clearance does not reach; a page of the `ashgrove-transit` account |
| `tickets/tickets.jsonl` | Tickets (JSON Lines export, uploaded as one page) | ticket organizations `7100001` (Ashgrove) and `7100002` (Brindle) have no mapping until an administrator maps them: held back, never unscoped |
| `slack/` | Slack workspace export | a private channel only one member belongs to; its name must never reach an administrator's setup or validation responses |

`sources.yaml` is an operator's sources file for the same documents (an operator-managed
source next to the product-managed ones); `sources-conflict.yaml` names the key of the
product-managed Documents source and must be refused (`source_config:managed_in_product`).

People (`@wrenfield.example`, identifiers are the verification IdP's subjects): Ada Quist,
the first administrator (`wrn-ada`, created by `org create`); Riley Moss (`wrn-riley`,
added by hand, assigned the Ashgrove account, in the private channel); Sam Okoro
(`wrn-sam`, added by hand, nothing assigned). The distinctive term **Quillon** appears
only in content Riley may read and Sam may not.
