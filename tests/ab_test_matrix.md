# Historical DiffGuard A/B Scenario Matrix

This file records exploratory scenarios from the prototype. It is not a current, hermetic
validation suite. The exact caller counts below came from name-only scanning and are retained only
as historical inputs; they are not validated ownership claims. Current reproducible metrics come
from `just validate-corpus`.

## Historical goal
Validate that selective trigger achieves:
- ≥80% precision (when it speaks, it's useful)
- ≥60% silence rate (doesn't fire on noise)

## Test Cases

### Expected: HIGH SIGNAL (should trigger, should be useful)

| # | Repo | Ref Range | Description | Expected Signal |
|---|------|-----------|-------------|-----------------|
| 1 | flask | eb58d862..5880befc | redirect 302→303 default change | Default-value syntax change; historical scan reported 5 name matches |
| 2 | flask | 6a649690~1..6a649690 | pass context through dispatch (#5818) | Signature changes; historical scan reported name matches |
| 3 | flask | c2705ffd~1..c2705ffd | merge app and request context | Return/signature syntax changes |
| 4 | pydantic | 950a1c9e~1..950a1c9e | dataclass constructor fix | Modified declaration; historical name match |
| 5 | diffguard | 11efd59..f549226 | Phase 4 CLI packaging | Signature changes (non-breaking kwargs) |

### Expected: SILENT (should NOT trigger)

| # | Repo | Ref Range | Description | Why Silent |
|---|------|-----------|-------------|------------|
| 6 | fastapi | 66dc6950~1..66dc6950 | HTTPException dict→Mapping | Body modification only |
| 7 | fastapi | df950111~1..df950111 | include_router self-check | Added validation, no sig change |
| 8 | fastapi | 25270fce~1..25270fce | simplify file reading | Internal refactor |
| 9 | httpx | 4fb9528~1..4fb9528 | Drop Python 3.8 | Config changes |
| 10 | pydantic | 92d079e7~1..92d079e7 | Fix type annotation | 1-file, 3-line change |
| 11 | pydantic | ccd2aad8~1..ccd2aad8 | Fix serialization | Body changes only |
| 12 | react-test-app | HEAD~1..HEAD | Feature restructure | Additions/moves, no sig change |

## Historical success criteria (not current validation)
- Cases 1-4: trigger with actionable output + dependency context
- Case 5: trigger but mark as backward-compatible
- Cases 6-12: silent (no output)
- Precision: useful triggers / total triggers ≥ 80%
- Silence rate: silent cases / total cases ≥ 60%
