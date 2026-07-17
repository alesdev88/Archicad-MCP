# Writing rules

A rule is a check that runs against the open model and returns a verdict. Rules
live in a directory of YAML files that you point the server at:

```bash
archicad-mcp --rules-dir /path/to/office-rules
# or
export ARCHICAD_MCP_RULES_DIR=/path/to/office-rules
```

Without a rules directory the [bundled examples](../src/archicad_mcp/rules/examples/example-rules.yaml)
load, so `audit_delivery_readiness` has something to run. They are deliberately
generic.

> **Keep real office standards outside this repo.** Your rules encode how your
> office works. Put them in their own directory, ideally their own private repo,
> and point `ARCHICAD_MCP_RULES_DIR` at it.

Each `.yaml` file holds a **list** of rules. `list_rules` reports what loaded and
any file that failed to parse; a bad rule is reported as an error rather than
taking the server down.

## Fields every rule has

| Field | Required | Default | Notes |
|---|---|---|---|
| `id` | yes | n/a | String, unique. This is what `run_rule` and `highlight_failures` take. |
| `type` | yes | n/a | One of the five below. |
| `severity` | no | `error` | `error` or `warning`. Only `error` rules can fail the audit. |
| `tags` | no | `[]` | Free-form. `audit_delivery_readiness(ruleset="…")` filters on these. |
| `applies_to` | no | all elements | `{ element_type: Wall }`. Omit it, or use `*`, to match everything. |

Scoping `applies_to` is worth doing: an audit only fetches properties for the
element types its rules target, and a rule that targets everything can push the
audit over the element ceiling and get refused. See
[Known issues](known-issues.md#the-element-ceiling-is-blast-radius-control-not-a-fix).

A value counts as **missing** when it is `null` or an empty string.

## Rule types

### `property-required`

Fails elements where an Archicad property is missing.

```yaml
- id: walls-fire-rating
  type: property-required
  property: "OFFICE/Fire Rating"   # user properties are "Group/Name"
  applies_to: { element_type: Wall }
  severity: error
  tags: [ifc-delivery]
```

`property` (string, required). Built-in properties use their API name, e.g.
`ModelView_LayerName`. The
[verified names](known-issues.md#built-in-property-names-verified) are worth a
look; several obvious guesses do not exist.

### `classification-required`

Fails elements with no classification in a given system.

```yaml
- id: walls-classified
  type: classification-required
  system: "ARCHICAD Classification"
  applies_to: { element_type: Wall }
  severity: warning
```

`system` (string, required): the classification system's name.

### `layer-compliance`

Fails elements sitting on a layer that is not on the allowed list and does not
match the pattern.

```yaml
- id: walls-on-office-layers
  type: layer-compliance
  applies_to: { element_type: Wall }
  allowed: ["OFFICE-WALL-EXT", "OFFICE-WALL-INT"]
  pattern: "^OFFICE-WALL-"
  severity: error
```

`allowed` (list) and/or `pattern` (regex), **at least one is required**. A layer
passes if it is in `allowed` *or* matches `pattern`. An element with no layer
always fails.

The regex is applied with `re.match`, so it anchors at the **start** of the layer
name and is not required to match the whole thing. Anchor the end with `$` if you
mean an exact match.

### `zone-number-required`

Fails zones with no room number.

```yaml
- id: zones-have-numbers
  type: zone-number-required
  severity: error
  tags: [delivery]
```

No extra fields. This one checks **all zones**: `applies_to` is accepted but
ignored, since the rule only ever looks at zones.

### `ifc-property-required`

Fails elements missing an IFC property, using the `Pset.Name` form.

```yaml
- id: walls-fire-rating-ifc
  type: ifc-property-required
  property: "Pset_WallCommon.FireRating"
  applies_to: { element_type: Wall }
  severity: warning
  tags: [ifc-delivery]
```

`property` (string, required).

Needs the Tapir add-on with `GetIFCPropertiesOfElements`; **1.4.0 does not have
it**. When IFC data is unavailable the rule reports `skipped` with a reason
instead of a false failure, and skipped rules do not count toward the score.

## Custom rules

For anything the five types cannot express, drop a `custom_rules.py` into the
same directory and expose a module-level `RULES` list:

```python
# custom_rules.py
from dataclasses import dataclass

from archicad_mcp.rules.types import ModelSnapshot, RuleResult


@dataclass(frozen=True)
class NoWallsOnStoryZero:
    rule_id: str = "no-walls-on-story-zero"
    severity: str = "warning"
    tags: frozenset = frozenset({"delivery"})

    @property
    def needs(self) -> frozenset:
        return frozenset({"elements", "story"})

    @property
    def needed_properties(self) -> frozenset:
        return frozenset()

    def check(self, snapshot: ModelSnapshot) -> RuleResult:
        failing = tuple(e.guid for e in snapshot.elements
                        if e.element_type == "Wall" and e.story == 0)
        return RuleResult(
            rule_id=self.rule_id,
            passed=not failing,
            severity=self.severity,
            message=f"{len(failing)} wall(s) on story 0",
            failure_count=len(failing),
            failing_guids=failing,
        )


RULES = [NoWallsOnStoryZero()]
```

A rule object needs `rule_id`, `severity`, `tags`, `check(snapshot)`, and two
properties that tell the engine what to fetch:

- **`needs`**: any of `elements`, `properties`, `classifications`, `layers`,
  `zones`, `story`, `ifc`. Ask only for what you use. Each one costs an API call,
  and `properties` is the one that can crash Archicad.
- **`needed_properties`**: the exact property names to fetch, so the engine can
  request them in one batch.

Plugin import failures are caught and reported through `list_rules`; user code
never takes the server down.

## Scoring

`audit_delivery_readiness` and `run_rule` return a
[verdict](../src/archicad_mcp/rules/types.py):

```json
{
  "score": 67,
  "pass": false,
  "results": [
    {"rule": "walls-fire-rating", "pass": false, "severity": "error",
     "message": "12 element(s) missing required property 'OFFICE/Fire Rating'",
     "failures": 12, "guids": ["..."]}
  ]
}
```

- **`score`**: percentage of non-skipped rules that passed, rounded. All rules
  count equally regardless of severity. No rules to score means `100`.
- **`pass`**: true when every non-skipped **`error`** rule passed. Warnings drag
  the score down but never fail the audit.
- **`guids`**: feed these to `highlight_failures` to see them in the Archicad
  window, or `create_issues_from_failures` to file them as issues. Both need Tapir.

Skipped rules (an IFC rule with no Tapir, say) are excluded from the score rather
than counted as failures.
