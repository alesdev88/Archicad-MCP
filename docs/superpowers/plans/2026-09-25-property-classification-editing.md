# Property and Classification Definition Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Edit custom property definitions and classification systems in place from chat (rename, regroup, availability, defaults, enum options, codes) and import Property / Classification Manager XML, without the Archicad dialogs.

**Architecture:** Five new or extended Tapir C++ commands in the local fork wrap `ACAPI_Property_ChangePropertyDefinition`, `ACAPI_Property_ChangePropertyGroup`, `ACAPI_Classification_Change*` and the two `*_Import` calls, each batch inside one undoable command. The MCP gets three dry-run-by-default tools that resolve human addresses (`Group/Name`, `System/Code`, enum display text) to GUIDs, show a before / after diff with warnings, then send one batch and re-read.

**Tech Stack:** C++ (Archicad 29 API DevKit 29.3000, CMake + Xcode), Python 3.12 (FastMCP, multiconn_archicad, pytest), uv.

**Spec:** `docs/superpowers/specs/2026-09-25-property-classification-editing-design.md` (read it before starting any task).

## Global Constraints

- Archicad 29 only; DevKit 29.3000 at `archicad-addon/Build/DevKits/AC29`, build recipe from `archicad-addon/Build/AC29` (see Task 7).
- Tapir fork: `/Users/alesd/Developer/tapir-archicad-automation`, `origin` is upstream ENZYME-APD. New work branch `feature/property-classification-editing` from `origin/main`.
- MCP repo: `/Users/alesd/Developer/Archicad MCP`, work branch `feature/definition-editing` from `main`.
- Commit identity in both repos: `Aleš Dolenec <285164556+alesdev88@users.noreply.github.com>`. Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- No em dashes or en dashes anywhere (code, comments, docs, commit messages). Rewrite the sentence instead.
- Tapir code style: 4-space indent, a space before `(` in calls and declarations, `GS::` types, commands register through `RegisterCommand<T> (group, version, description)`. New commands register under version `"1.5.9"` (the fork's `ADDON_VERSION`).
- Every edit keeps GUIDs: read the struct, patch only the fields sent, call `Change*`. Unsent fields stay untouched.
- Refused, always: built-in properties and groups, value / collection / measure type changes, classification reparenting.
- No MCP code path in this plan may call `GetPropertyValuesOfElements` (the Archicad 29 property-read crash).
- Live Archicad work only on Oprema-objekti, port 19724. Never CVP (19723) without Aleš's explicit go-ahead in chat.
- Nothing from office projects in the public MCP repo: XML fixtures are trimmed to the "MCP Test" group and "MCP Test" system before commit.
- Before any build, all Archicad instances are quit (including the Teamwork twin) and the installed bundle is backed up.

## Review Focus

1. **Property or group names containing `/`**: `Group/Name` must match against the full address strings, never by splitting on `/`. Test in Task 9 (`test_resolve_property_whose_group_contains_a_slash`).
2. **Two enum options with the same display text**: any rename / remove / default naming that text must be an error in the plan, not a guess. Test in Task 10 (`test_enum_text_matching_two_options_is_an_error`).
3. **Removing the enum option that is the current default without sending a new default**: refused in the plan and in C++. Tests in Task 10 (`test_removing_the_default_option_needs_a_new_default`) and Task 2 example.
4. **Classification system names that are prefixes of each other** (`ELEA` and `ELEA 2`): `System/Code` resolution must take the longest matching system name. Test in Task 8 (`test_longest_system_name_wins`).
5. **An `UpdatePropertyDefinitions` call against an old Tapir (1.5.4 to 1.5.9 upstream) that silently ignores the new fields**: the capability gate must refuse before sending. Test in Task 9 (`test_old_tapir_is_refused_before_anything_is_sent`).

---

## Phase A: Tapir fork (C++)

### Task 1: Prerequisites and captured reference exports

**Files:**
- Create (scratch, not committed yet): `/private/tmp/claude-1209053691/-Users-alesd-Developer-Archicad-MCP/70a6ca64-ff45-4c35-8fef-2286a6de6d84/scratchpad/exports/properties.xml`, `.../exports/classifications.xml`

This task has user actions. Ask Aleš in chat and wait for each.

- [ ] **Step 1: Xcode license (Aleš runs it).** Ask him to run `sudo xcodebuild -license` in a terminal. Then verify:

Run: `git --version && xcodebuild -version`
Expected: both print versions, no "You have not agreed to the Xcode license" text.

- [ ] **Step 2: The uncommitted change on `feature/section-dimension-chains`.** Show him the diff and ask: commit it, or discard it.

Run: `git -C /Users/alesd/Developer/tapir-archicad-automation diff archicad-addon/Sources/ExtendedElementCommands.cpp`

If commit: `git -C /Users/alesd/Developer/tapir-archicad-automation commit -am "<his wording>"` after `git checkout archicad-addon/Test/TestProject.pla` restores the file the sandbox deleted (the deletion must not be committed). If discard: `git checkout archicad-addon/Sources/ExtendedElementCommands.cpp archicad-addon/Test/TestProject.pla`.

- [ ] **Step 3: Reference exports (Aleš does this in Archicad on Oprema-objekti).** Ask him to export from Options > Property Manager (Export, all custom groups) to `.../scratchpad/exports/properties.xml` and from Options > Classification Manager (Export, the "MCP Test" system) to `.../scratchpad/exports/classifications.xml`. These pin the XML dialect for Task 5 and Task 12.

- [ ] **Step 4: Read both files** and write down, in a note appended to this task, the actual tag names for: property group element and its name child; property definition element and its name child; classification system element and its name child; classification item element and its code child. Task 12's constants come from this note.

---

### Task 2: Default-value parser helper and extended `UpdatePropertyDefinitions`

**Files:**
- Modify: `archicad-addon/Sources/PropertyCommands.cpp` (CreatePropertyDefinitions default parsing, lines ~1141-1284; UpdatePropertyDefinitions, lines ~1418-1594)
- Modify: `archicad-addon/Sources/CommandBase.hpp`, `archicad-addon/Sources/CommandBase.cpp` (error text helper)
- Create: `archicad-addon/Examples/update_property_definitions.py`

**Interfaces:**
- Produces (C++, file-static in PropertyCommands.cpp): `static bool ParseDefaultValue (const GS::ObjectState& defaultValue, API_PropertyDefinition& definition, GS::UniString& error)`; `static bool ApplyEnumEdits (const GS::ObjectState& item, API_PropertyDefinition& definition, GS::UniString& error)`; `static bool ApplyAvailabilityEdits (const GS::ObjectState& item, API_PropertyDefinition& definition, GS::UniString& error)`.
- Produces (CommandBase): `GS::UniString DescribeDefinitionChangeError (GSErrCode err);`
- Produces (JSON): `UpdatePropertyDefinitions` item fields `name`, `description`, `groupId {guid}`, `defaultValue` (PropertyDefaultValue shape), `availability {add|remove|set: [{classificationItemId:{guid}}]}`, `renameEnumValues [{enumValueId:{guid}, displayValue, nonLocalizedValue?}]`, `removeEnumValues [{enumValueId:{guid}}]`, `enumOrder [string]`, plus the existing `expressions` and `possibleEnumValues`.

- [ ] **Step 1: Create the branch**

```bash
cd /Users/alesd/Developer/tapir-archicad-automation
git fetch origin
git switch -c feature/property-classification-editing origin/main
```

- [ ] **Step 2: Add the error text helper.** In `CommandBase.hpp`, after `GS::ObjectState CreateSuccessfulExecutionResult ();` add:

```cpp
GS::UniString DescribeDefinitionChangeError (GSErrCode err);
```

In `CommandBase.cpp`, after `CreateSuccessfulExecutionResult`:

```cpp
// Plain wording for the errors the Change* / Import property and classification calls
// return, so a caller sees why a definition edit was refused without an error table.
GS::UniString DescribeDefinitionChangeError (GSErrCode err)
{
    switch (err) {
        case APIERR_NOACCESSRIGHT:   return "no access right: in Teamwork this needs the right to modify properties or classifications";
        case APIERR_NAMEALREADYUSED: return "the name is already used in the target group or system";
        case APIERR_BADVALUE:        return "an enum option or the default value is not one of the allowed options";
        case APIERR_BADID:           return "an id does not refer to an existing property, group or classification item";
        case APIERR_BADPARS:         return "inconsistent definition, for example a default value that does not match the property type";
        default:                     return GS::UniString::Printf ("Archicad refused the change (error %d)", (int) err);
    }
}
```

- [ ] **Step 3: Extract `ParseDefaultValue`.** Above `CreatePropertyDefinitionsCommand::CreatePropertyDefinitionsCommand`, add the helper. Its body is the existing default-value block of `CreatePropertyDefinitionsCommand::Execute` (from `apiPropertyDefinition.defaultValue.hasExpression = false;` down to the closing `}` of the `userUndefined` / null branch), moved with these exact substitutions:
  - `apiPropertyDefinition` becomes `definition`;
  - every `propertyIds (CreateErrorResponse (APIERR_BADPARS, MSG)); continue;` becomes `error = MSG; return false;`;
  - inside the multiEnum loop, `failed = true; propertyIds (CreateErrorResponse (APIERR_BADPARS, MSG)); break;` becomes `error = MSG; return false;` and the `failed` flag and its `if (failed) continue;` are deleted;
  - `switch (std::get<0> (*typeTuple))` becomes `switch (definition.collectionType)`, and the lookup of `basicDefaultValue/type` plus its two error branches are deleted (the definition already knows its type; `type` stays accepted by the schema and is ignored).

The helper's frame:

```cpp
// Fills definition.defaultValue from a PropertyDefaultValue object: {expressions: [...]}
// or {basicDefaultValue: {...}}. Enum defaults are resolved against
// definition.possibleEnumValues, so apply enum edits before calling this.
static bool ParseDefaultValue (const GS::ObjectState& defaultValue, API_PropertyDefinition& definition, GS::UniString& error)
{
    GS::Array<GS::UniString> expressions;
    defaultValue.Get ("expressions", expressions);
    if (!expressions.IsEmpty ()) {
        definition.defaultValue.hasExpression = true;
        definition.defaultValue.propertyExpressions = expressions;
        return true;
    }

    const GS::ObjectState* basicDefaultValue = defaultValue.Get ("basicDefaultValue");
    if (basicDefaultValue == nullptr) {
        error = "both defaultValue/basicDefaultValue and defaultValue/expressions are missing or empty";
        return false;
    }

    definition.defaultValue.hasExpression = false;
    definition.defaultValue.propertyExpressions.Clear ();
    definition.defaultValue.basicValue = API_PropertyValue ();
    definition.defaultValue.basicValue.singleVariant.variant.type = definition.valueType;

    GS::UniString statusStr;
    if (!basicDefaultValue->Get ("status", statusStr) || statusStr.IsEmpty ()) {
        error = "defaultValue/basicDefaultValue/status is missing or empty";
        return false;
    }

    if (statusStr == "normal") {
        definition.defaultValue.basicValue.variantStatus = API_VariantStatusNormal;
        switch (definition.collectionType) {
            // ... the moved cases, substituted as listed above ...
        }
    } else if (statusStr == "userUndefined") {
        definition.defaultValue.basicValue.variantStatus = API_VariantStatusUserUndefined;
    } else {
        definition.defaultValue.basicValue.variantStatus = API_VariantStatusNull;
    }
    return true;
}
```

Replace the moved block in `CreatePropertyDefinitionsCommand::Execute` with:

```cpp
            apiPropertyDefinition.defaultValue.hasExpression = false;
            apiPropertyDefinition.defaultValue.basicValue.singleVariant.variant.type = apiPropertyDefinition.valueType;
            apiPropertyDefinition.defaultValue.basicValue.variantStatus = API_VariantStatusNull;
            const GS::ObjectState* defaultValue = propertyDefinition->Get ("defaultValue");
            if (defaultValue != nullptr) {
                GS::UniString defaultError;
                if (!ParseDefaultValue (*defaultValue, apiPropertyDefinition, defaultError)) {
                    propertyIds (CreateErrorResponse (APIERR_BADPARS, defaultError));
                    continue;
                }
            }
```

- [ ] **Step 4: Add `ApplyEnumEdits`** (above `UpdatePropertyDefinitionsCommand`). It takes over the append loop currently inline in `UpdatePropertyDefinitionsCommand::Execute`, unchanged in behaviour, and adds rename, remove and order, applied in that order: rename, remove, add, order.

```cpp
static bool ApplyEnumEdits (const GS::ObjectState& item, API_PropertyDefinition& definition, GS::UniString& error)
{
    GS::Array<GS::ObjectState> renames, removes, adds;
    GS::Array<GS::UniString> order;
    const bool hasRenames = item.Get ("renameEnumValues", renames);
    const bool hasRemoves = item.Get ("removeEnumValues", removes);
    const bool hasAdds = item.Get ("possibleEnumValues", adds);
    const bool hasOrder = item.Get ("enumOrder", order);
    if (!hasRenames && !hasRemoves && !hasAdds && !hasOrder) {
        return true;
    }

    if (definition.collectionType != API_PropertySingleChoiceEnumerationCollectionType &&
        definition.collectionType != API_PropertyMultipleChoiceEnumerationCollectionType) {
        error = "property is not an enumeration";
        return false;
    }

    auto indexOfGuid = [&] (const API_Guid& guid) -> Int32 {
        for (UIndex i = 0; i < definition.possibleEnumValues.GetSize (); ++i) {
            if (definition.possibleEnumValues[i].keyVariant.guidValue == guid) {
                return (Int32) i;
            }
        }
        return -1;
    };

    // Renames keep the option's guid, so element values holding it follow the new text.
    for (const GS::ObjectState& r : renames) {
        const GS::ObjectState* enumValueId = r.Get ("enumValueId");
        GS::UniString displayValue;
        if (enumValueId == nullptr || !r.Get ("displayValue", displayValue)) {
            error = "a renameEnumValues entry needs enumValueId and displayValue";
            return false;
        }
        const Int32 i = indexOfGuid (GetGuidFromObjectState (*enumValueId));
        if (i < 0) {
            error = "renameEnumValues: enumValueId is not an option of this property";
            return false;
        }
        definition.possibleEnumValues[i].displayVariant.uniStringValue = displayValue;
        GS::UniString nonLocalizedValue;
        if (r.Get ("nonLocalizedValue", nonLocalizedValue)) {
            definition.possibleEnumValues[i].nonLocalizedValue = nonLocalizedValue;
        }
    }

    for (const GS::ObjectState& r : removes) {
        const GS::ObjectState* enumValueId = r.Get ("enumValueId");
        if (enumValueId == nullptr) {
            error = "a removeEnumValues entry needs enumValueId";
            return false;
        }
        const Int32 i = indexOfGuid (GetGuidFromObjectState (*enumValueId));
        if (i < 0) {
            error = "removeEnumValues: enumValueId is not an option of this property";
            return false;
        }
        definition.possibleEnumValues.Delete ((UIndex) i);
    }

    // Appending is the 1.5.4 behaviour, kept as it was: a value already on the property,
    // matched by nonLocalizedValue or displayValue, is not added twice.
    for (const GS::ObjectState& e : adds) {
        const GS::ObjectState* enumValue = e.Get ("enumValue");
        if (enumValue == nullptr) {
            error = "an enumValue is missing or has no displayValue";
            return false;
        }
        API_SingleEnumerationVariant variant;
        variant.displayVariant.type = definition.valueType;
        variant.keyVariant.type = API_PropertyGuidValueType;
        if (!enumValue->Get ("displayValue", variant.displayVariant.uniStringValue)) {
            error = "an enumValue is missing or has no displayValue";
            return false;
        }
        GS::UniString nonLocalizedValueStr;
        if (enumValue->Get ("nonLocalizedValue", nonLocalizedValueStr)) {
            variant.nonLocalizedValue = nonLocalizedValueStr;
        }
        const bool alreadyThere =
            (variant.nonLocalizedValue.HasValue () &&
             FindEnumValueGuid (definition.possibleEnumValues, "nonLocalizedValue", *variant.nonLocalizedValue) != APINULLGuid) ||
            FindEnumValueGuid (definition.possibleEnumValues, "displayValue", variant.displayVariant.uniStringValue) != APINULLGuid;
        if (alreadyThere) {
            continue;
        }
        variant.keyVariant.guidValue = GetRandomGuid ();
        definition.possibleEnumValues.Push (variant);
    }

    // enumOrder is by display text after the edits above, and must name every option once.
    if (hasOrder) {
        if (order.GetSize () != definition.possibleEnumValues.GetSize ()) {
            error = "enumOrder must list every option exactly once";
            return false;
        }
        GS::Array<API_SingleEnumerationVariant> reordered;
        for (const GS::UniString& text : order) {
            Int32 found = -1;
            for (UIndex i = 0; i < definition.possibleEnumValues.GetSize (); ++i) {
                if (definition.possibleEnumValues[i].displayVariant.uniStringValue == text) {
                    if (found >= 0) {
                        error = "enumOrder: two options share the text '" + text + "'";
                        return false;
                    }
                    found = (Int32) i;
                }
            }
            if (found < 0) {
                error = "enumOrder: '" + text + "' is not an option of this property";
                return false;
            }
            for (const API_SingleEnumerationVariant& already : reordered) {
                if (already.keyVariant.guidValue == definition.possibleEnumValues[found].keyVariant.guidValue) {
                    error = "enumOrder must list every option exactly once";
                    return false;
                }
            }
            reordered.Push (definition.possibleEnumValues[found]);
        }
        definition.possibleEnumValues = reordered;
    }

    return true;
}
```

- [ ] **Step 5: Add `ApplyAvailabilityEdits`.**

```cpp
static bool ApplyAvailabilityEdits (const GS::ObjectState& item, API_PropertyDefinition& definition, GS::UniString& error)
{
    const GS::ObjectState* availability = item.Get ("availability");
    if (availability == nullptr) {
        return true;
    }

    GS::Array<GS::ObjectState> set, add, remove;
    const bool hasSet = availability->Get ("set", set);
    const bool hasAdd = availability->Get ("add", add);
    const bool hasRemove = availability->Get ("remove", remove);
    if (hasSet && (hasAdd || hasRemove)) {
        error = "availability takes either set, or add and/or remove";
        return false;
    }

    auto guidsOf = [] (const GS::Array<GS::ObjectState>& list) {
        GS::Array<API_Guid> guids;
        for (const GS::ObjectState& os : list) {
            const GS::ObjectState* id = os.Get ("classificationItemId");
            if (id != nullptr) {
                guids.Push (GetGuidFromObjectState (*id));
            }
        }
        return guids;
    };

    if (hasSet) {
        definition.availability = guidsOf (set);
        return true;
    }

    for (const API_Guid& guid : guidsOf (add)) {
        if (!definition.availability.Contains (guid)) {
            definition.availability.Push (guid);
        }
    }
    const GS::Array<API_Guid> toRemove = guidsOf (remove);
    GS::Array<API_Guid> kept;
    for (const API_Guid& guid : definition.availability) {
        if (!toRemove.Contains (guid)) {
            kept.Push (guid);
        }
    }
    definition.availability = kept;
    return true;
}
```

- [ ] **Step 6: Rewrite `UpdatePropertyDefinitionsCommand::GetInputParametersSchema`** so each item accepts the new fields:

```cpp
GS::Optional<GS::UniString> UpdatePropertyDefinitionsCommand::GetInputParametersSchema () const
{
    return R"({
        "type": "object",
        "properties": {
            "propertyDefinitions": {
                "type": "array",
                "description": "The property definitions to update. Only the fields given change; the definition keeps its guid, so element values survive.",
                "items": {
                    "type": "object",
                    "properties": {
                        "propertyId": { "$ref": "#/PropertyId" },
                        "name": { "type": "string", "description": "New name of the property." },
                        "description": { "type": "string", "description": "New description of the property." },
                        "groupId": { "$ref": "#/PropertyGroupId", "description": "Move the property into this custom property group." },
                        "defaultValue": { "$ref": "#/PropertyDefaultValue", "description": "New default value: a basic value or expressions. Switching between the two is allowed." },
                        "expressions": {
                            "type": "array",
                            "description": "The new expression strings for the property. Only for expression-based properties.",
                            "items": { "type": "string" },
                            "minItems": 1
                        },
                        "availability": {
                            "type": "object",
                            "description": "Classification items the property is available for: set replaces the list, add and remove edit it.",
                            "properties": {
                                "set": { "type": "array", "items": { "$ref": "#/ClassificationItemIdArrayItem" } },
                                "add": { "type": "array", "items": { "$ref": "#/ClassificationItemIdArrayItem" } },
                                "remove": { "type": "array", "items": { "$ref": "#/ClassificationItemIdArrayItem" } }
                            },
                            "additionalProperties": false
                        },
                        "possibleEnumValues": {
                            "$ref": "#/EnumValuesToAdd",
                            "description": "The enum values to add to an enumeration property. Values already on the property keep their identifier, so element values assigned to them survive; values not listed here are kept as well."
                        },
                        "renameEnumValues": {
                            "type": "array",
                            "description": "Change the text of existing enum options. The option keeps its identifier, so element values follow the new text.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "enumValueId": { "$ref": "#/Guid" },
                                    "displayValue": { "type": "string" },
                                    "nonLocalizedValue": { "type": "string" }
                                },
                                "additionalProperties": false,
                                "required": [ "enumValueId", "displayValue" ]
                            }
                        },
                        "removeEnumValues": {
                            "type": "array",
                            "description": "Enum options to remove. Elements holding a removed option lose that value.",
                            "items": {
                                "type": "object",
                                "properties": { "enumValueId": { "$ref": "#/Guid" } },
                                "additionalProperties": false,
                                "required": [ "enumValueId" ]
                            }
                        },
                        "enumOrder": {
                            "type": "array",
                            "description": "Every option's display text, once, in the new order (applied after rename, remove and add).",
                            "items": { "type": "string" }
                        }
                    },
                    "additionalProperties": false,
                    "required": [ "propertyId" ]
                }
            }
        },
        "additionalProperties": false,
        "required": [ "propertyDefinitions" ]
    })";
}
```

`enumValueId` here is `{"guid": "..."}`. Check the shared `Guid` definition: if `#/Guid` is a plain string, use an inline `{"type":"object","properties":{"guid":{"$ref":"#/Guid"}},"required":["guid"],"additionalProperties":false}` instead, so the wire shape stays `{"enumValueId":{"guid":"..."}}` as `GetGuidFromObjectState` expects.

- [ ] **Step 7: Rewrite `UpdatePropertyDefinitionsCommand::Execute`.**

```cpp
GS::ObjectState UpdatePropertyDefinitionsCommand::Execute (const GS::ObjectState& parameters, GS::ProcessControl& /*processControl*/) const
{
    GS::Array<GS::ObjectState> propertyDefinitions;
    parameters.Get ("propertyDefinitions", propertyDefinitions);

    GS::ObjectState response;
    const auto& executionResults = response.AddList<GS::ObjectState> ("executionResults");

    ACAPI_CallUndoableCommand ("UpdatePropertyDefinitions", [&]() -> GSErrCode {
        for (const GS::ObjectState& item : propertyDefinitions) {
            const GS::ObjectState* propertyId = item.Get ("propertyId");
            if (propertyId == nullptr) {
                executionResults (CreateFailedExecutionResult (APIERR_BADPARS, "propertyId is missing"));
                continue;
            }

            API_PropertyDefinition definition;
            definition.guid = GetGuidFromObjectState (*propertyId);
            if (ACAPI_Property_GetPropertyDefinition (definition) != NoError) {
                executionResults (CreateFailedExecutionResult (APIERR_BADPARS, "property not found"));
                continue;
            }
            if (definition.definitionType != API_PropertyCustomDefinitionType) {
                executionResults (CreateFailedExecutionResult (APIERR_BADPARS, "built-in properties cannot be changed"));
                continue;
            }

            item.Get ("name", definition.name);
            item.Get ("description", definition.description);
            const GS::ObjectState* groupId = item.Get ("groupId");
            if (groupId != nullptr) {
                definition.groupGuid = GetGuidFromObjectState (*groupId);
            }

            GS::UniString error;
            if (!ApplyEnumEdits (item, definition, error) || !ApplyAvailabilityEdits (item, definition, error)) {
                executionResults (CreateFailedExecutionResult (APIERR_BADPARS, error));
                continue;
            }

            GS::Array<GS::UniString> expressions;
            const bool hasExpressions = item.Get ("expressions", expressions);
            const GS::ObjectState* defaultValue = item.Get ("defaultValue");
            if (hasExpressions && defaultValue != nullptr) {
                executionResults (CreateFailedExecutionResult (APIERR_BADPARS, "send expressions or defaultValue, not both"));
                continue;
            }
            if (hasExpressions) {
                if (!definition.defaultValue.hasExpression) {
                    executionResults (CreateFailedExecutionResult (APIERR_BADPARS, "property is not expression-based"));
                    continue;
                }
                definition.defaultValue.propertyExpressions = expressions;
            }
            if (defaultValue != nullptr && !ParseDefaultValue (*defaultValue, definition, error)) {
                executionResults (CreateFailedExecutionResult (APIERR_BADPARS, error));
                continue;
            }

            // A removed option can still be the default. Archicad would answer APIERR_BADVALUE;
            // say which rule was broken instead.
            if (!definition.defaultValue.hasExpression &&
                definition.defaultValue.basicValue.variantStatus == API_VariantStatusNormal &&
                definition.collectionType == API_PropertySingleChoiceEnumerationCollectionType) {
                bool defaultStillThere = false;
                for (const API_SingleEnumerationVariant& v : definition.possibleEnumValues) {
                    if (v.keyVariant.guidValue == definition.defaultValue.basicValue.singleVariant.variant.guidValue) {
                        defaultStillThere = true;
                    }
                }
                if (!defaultStillThere) {
                    executionResults (CreateFailedExecutionResult (APIERR_BADPARS, "the default value is an option being removed; send a new defaultValue in the same item"));
                    continue;
                }
            }

            const GSErrCode err = ACAPI_Property_ChangePropertyDefinition (definition);
            if (err != NoError) {
                executionResults (CreateFailedExecutionResult (err, DescribeDefinitionChangeError (err)));
                continue;
            }
            executionResults (CreateSuccessfulExecutionResult ());
        }
        return NoError;
    });

    return response;
}
```

The check covers singleEnum defaults only. A multiEnum default that lists a removed option is left to Archicad, which answers `APIERR_BADVALUE`, reported through `DescribeDefinitionChangeError`; the MCP plan (Task 10) refuses that case before sending anyway.

- [ ] **Step 8: Update the registration text** in `AddOnMain.cpp`:

```cpp
        err |= RegisterCommand<UpdatePropertyDefinitionsCommand> (
            propertyCommands, "1.5.4",
            "Updates existing Custom Property Definitions in place, keeping their guid: name, description, group, default value or expressions, availability, and enum options (add, rename, remove, reorder)."
        );
```

- [ ] **Step 9: Write the example** `archicad-addon/Examples/update_property_definitions.py`. It creates its own group and enum property, edits every new field, checks the guid and option ids survive, and deletes what it made:

```python
import aclib

# Every field of UpdatePropertyDefinitions on a property this script creates and removes
# again, so it runs on any project. The point it checks: the property and its enum
# options keep their identifiers through rename, regroup and option edits, which is
# what keeps the values elements already hold.

GROUP_A = 'Tapir Example Update A'
GROUP_B = 'Tapir Example Update B'


def Find (name):
    for p in aclib.RunTapirCommand ('GetAllProperties', {}, debug = False)['properties']:
        if p['propertyType'] == 'Custom' and p['propertyName'] == name:
            return p
    return None


def Options (p):
    return {v['enumValue']['displayValue']: v['enumValue']['guid'] for v in p.get ('possibleEnumValues', [])}


groups = aclib.RunTapirCommand ('CreatePropertyGroups', {'propertyGroups': [
    {'propertyGroup': {'name': GROUP_A}}, {'propertyGroup': {'name': GROUP_B}}]}, debug = False)
groupB = groups['propertyGroupIds'][1]['propertyGroupId']

created = aclib.RunTapirCommand ('CreatePropertyDefinitions', {'propertyDefinitions': [{'propertyDefinition': {
    'name': 'Status', 'description': 'before', 'type': 'singleEnum', 'isEditable': True,
    'availability': [], 'group': {'name': GROUP_A},
    'possibleEnumValues': [{'enumValue': {'displayValue': v}} for v in ('Draft', 'Old', 'Approved')],
    'defaultValue': {'basicDefaultValue': {'status': 'normal', 'type': 'singleEnum',
                                           'value': {'type': 'displayValue', 'displayValue': 'Draft'}}}}}]}, debug = False)
propertyId = created['propertyIds'][0]['propertyId']
before = Options (Find ('Status'))

result = aclib.RunTapirCommand ('UpdatePropertyDefinitions', {'propertyDefinitions': [{
    'propertyId': propertyId,
    'name': 'Approval',
    'description': 'after',
    'groupId': groupB,
    'renameEnumValues': [{'enumValueId': {'guid': before['Draft']}, 'displayValue': 'In progress'}],
    'removeEnumValues': [{'enumValueId': {'guid': before['Old']}}],
    'possibleEnumValues': [{'enumValue': {'displayValue': 'Rejected'}}],
    'enumOrder': ['Approved', 'In progress', 'Rejected'],
    'defaultValue': {'basicDefaultValue': {'status': 'normal', 'type': 'singleEnum',
                                           'value': {'type': 'displayValue', 'displayValue': 'Approved'}}}}]}, debug = False)
print ('Update succeeded: {}'.format (result['executionResults'][0]['success']))

after = Find ('Approval')
print ('Same property guid: {}'.format (after['propertyId'] == propertyId))
print ('Group now: {}'.format (after['propertyGroupName']))
print ('Options now: {}'.format (list (Options (after))))
print ('Renamed option kept its id: {}'.format (Options (after)['In progress'] == before['Draft']))

refused = aclib.RunTapirCommand ('UpdatePropertyDefinitions', {'propertyDefinitions': [{
    'propertyId': propertyId,
    'removeEnumValues': [{'enumValueId': {'guid': before['Approved']}}]}]}, debug = False)
print ('Removing the default option alone is refused: {}'.format (not refused['executionResults'][0]['success']))

aclib.RunTapirCommand ('DeletePropertyDefinitions', {'propertyIds': [{'propertyId': propertyId}]}, debug = False)
aclib.RunTapirCommand ('DeletePropertyGroups', {'propertyGroupIds': [
    groups['propertyGroupIds'][0], groups['propertyGroupIds'][1]]}, debug = False)
```

Expected output once built (checked in Task 7):

```
Update succeeded: True
Same property guid: True
Group now: Tapir Example Update B
Options now: ['Approved', 'In progress', 'Rejected']
Renamed option kept its id: True
Removing the default option alone is refused: True
```

- [ ] **Step 10: Compile check** (no install yet; Task 7 installs).

Run: `cmake --build archicad-addon/Build/AC29 --config RelWithDebInfo 2>&1 | tail -5`
Expected: `** BUILD SUCCEEDED **`. If the build dir does not exist yet, run the two setup commands from Task 7 Step 1 first.

- [ ] **Step 11: Commit**

```bash
git add archicad-addon/Sources/PropertyCommands.cpp archicad-addon/Sources/CommandBase.hpp archicad-addon/Sources/CommandBase.cpp archicad-addon/Sources/AddOnMain.cpp archicad-addon/Examples/update_property_definitions.py
git commit -m "feat: UpdatePropertyDefinitions edits name, group, default, availability and enum options

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `UpdatePropertyGroups`

**Files:**
- Modify: `archicad-addon/Sources/PropertyCommands.hpp`, `PropertyCommands.cpp`, `AddOnMain.cpp`
- Create: `archicad-addon/Examples/update_property_groups.py`

**Interfaces:**
- Consumes: `DescribeDefinitionChangeError` (Task 2).
- Produces (JSON): input `{"propertyGroups": [{"propertyGroupId": {guid}, "name"?, "description"?}]}`, response `{"executionResults": [...]}`.

- [ ] **Step 1: Declare** in `PropertyCommands.hpp`, after `UpdatePropertyDefinitionsCommand`:

```cpp
class UpdatePropertyGroupsCommand : public CommandBase
{
public:
    UpdatePropertyGroupsCommand ();
    virtual GS::String GetName () const override;
    virtual GS::Optional<GS::UniString> GetInputParametersSchema () const override;
    virtual GS::Optional<GS::UniString> GetRawResponseSchema () const override;
    virtual GS::ObjectState Execute (const GS::ObjectState& parameters, GS::ProcessControl& processControl) const override;
};
```

- [ ] **Step 2: Implement** at the end of `PropertyCommands.cpp`:

```cpp
UpdatePropertyGroupsCommand::UpdatePropertyGroupsCommand () :
    CommandBase (CommonSchema::Used)
{}

GS::String UpdatePropertyGroupsCommand::GetName () const
{
    return "UpdatePropertyGroups";
}

GS::Optional<GS::UniString> UpdatePropertyGroupsCommand::GetInputParametersSchema () const
{
    return R"({
        "type": "object",
        "properties": {
            "propertyGroups": {
                "type": "array",
                "description": "The custom property groups to update. Only the fields given change.",
                "items": {
                    "type": "object",
                    "properties": {
                        "propertyGroupId": { "$ref": "#/PropertyGroupId" },
                        "name": { "type": "string" },
                        "description": { "type": "string" }
                    },
                    "additionalProperties": false,
                    "required": [ "propertyGroupId" ]
                }
            }
        },
        "additionalProperties": false,
        "required": [ "propertyGroups" ]
    })";
}

GS::Optional<GS::UniString> UpdatePropertyGroupsCommand::GetRawResponseSchema () const
{
    return R"({
        "type": "object",
        "properties": { "executionResults": { "$ref": "#/ExecutionResults" } },
        "additionalProperties": false,
        "required": [ "executionResults" ]
    })";
}

GS::ObjectState UpdatePropertyGroupsCommand::Execute (const GS::ObjectState& parameters, GS::ProcessControl& /*processControl*/) const
{
    GS::Array<GS::ObjectState> propertyGroups;
    parameters.Get ("propertyGroups", propertyGroups);

    GS::ObjectState response;
    const auto& executionResults = response.AddList<GS::ObjectState> ("executionResults");

    ACAPI_CallUndoableCommand ("UpdatePropertyGroups", [&]() -> GSErrCode {
        for (const GS::ObjectState& item : propertyGroups) {
            const GS::ObjectState* groupId = item.Get ("propertyGroupId");
            if (groupId == nullptr) {
                executionResults (CreateFailedExecutionResult (APIERR_BADPARS, "propertyGroupId is missing"));
                continue;
            }
            API_PropertyGroup group;
            group.guid = GetGuidFromObjectState (*groupId);
            if (ACAPI_Property_GetPropertyGroup (group) != NoError) {
                executionResults (CreateFailedExecutionResult (APIERR_BADID, "property group not found"));
                continue;
            }
            if (group.groupType != API_PropertyCustomGroupType) {
                executionResults (CreateFailedExecutionResult (APIERR_BADPARS, "built-in property groups cannot be changed"));
                continue;
            }
            item.Get ("name", group.name);
            item.Get ("description", group.description);
            const GSErrCode err = ACAPI_Property_ChangePropertyGroup (group);
            if (err != NoError) {
                executionResults (CreateFailedExecutionResult (err, DescribeDefinitionChangeError (err)));
                continue;
            }
            executionResults (CreateSuccessfulExecutionResult ());
        }
        return NoError;
    });

    return response;
}
```

- [ ] **Step 3: Register** in `AddOnMain.cpp`, after `UpdatePropertyDefinitionsCommand`:

```cpp
        err |= RegisterCommand<UpdatePropertyGroupsCommand> (
            propertyCommands, "1.5.9",
            "Updates the name and/or description of existing Custom Property Groups, keeping their guid."
        );
```

- [ ] **Step 4: Example** `archicad-addon/Examples/update_property_groups.py`:

```python
import aclib

# Renames a property group this script creates, then removes it. The group keeps its
# identifier, so the properties inside it stay where they are.

created = aclib.RunTapirCommand ('CreatePropertyGroups', {'propertyGroups': [
    {'propertyGroup': {'name': 'Tapir Example Group Before'}}]}, debug = False)
groupId = created['propertyGroupIds'][0]['propertyGroupId']

result = aclib.RunTapirCommand ('UpdatePropertyGroups', {'propertyGroups': [
    {'propertyGroupId': groupId, 'name': 'Tapir Example Group After', 'description': 'renamed'}]}, debug = False)
print ('Rename succeeded: {}'.format (result['executionResults'][0]['success']))

groups = aclib.RunTapirCommand ('GetAllProperties', {}, debug = False).get ('propertyGroups', [])
print ('Found under the new name: {}'.format (any (g['name'] == 'Tapir Example Group After' for g in groups)))

aclib.RunTapirCommand ('DeletePropertyGroups', {'propertyGroupIds': [{'propertyGroupId': groupId}]}, debug = False)
```

(`propertyGroups` in `GetAllProperties` comes in Task 6; until then the second line prints `False`, which is expected at this point.)

- [ ] **Step 5: Compile check.** Run: `cmake --build archicad-addon/Build/AC29 --config RelWithDebInfo 2>&1 | tail -5`. Expected: `** BUILD SUCCEEDED **`.

- [ ] **Step 6: Commit**

```bash
git add archicad-addon/Sources/PropertyCommands.hpp archicad-addon/Sources/PropertyCommands.cpp archicad-addon/Sources/AddOnMain.cpp archicad-addon/Examples/update_property_groups.py
git commit -m "feat: UpdatePropertyGroups renames custom property groups in place

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `UpdateClassificationSystems` and `UpdateClassificationItems`

**Files:**
- Modify: `archicad-addon/Sources/ClassificationCommands.hpp`, `ClassificationCommands.cpp`, `AddOnMain.cpp`
- Create: `archicad-addon/Examples/update_classifications.py`

**Interfaces:**
- Consumes: `DescribeDefinitionChangeError` (Task 2).
- Produces (JSON): `UpdateClassificationSystems` input `{"classificationSystems": [{"classificationSystemId": {guid}, "name"?, "description"?, "source"?, "version"?, "date"? ("YYYY-MM-DD")}]}`; `UpdateClassificationItems` input `{"classificationItems": [{"classificationItemId": {guid}, "id"?, "name"?, "description"?}]}`; both respond `{"executionResults": [...]}`. The MCP marker command is `UpdateClassificationItems`.

- [ ] **Step 1: Declare** both classes in `ClassificationCommands.hpp` after `DeleteClassificationItemsCommand`, same shape as in Task 3 Step 1 (`UpdateClassificationSystemsCommand`, `UpdateClassificationItemsCommand`).

- [ ] **Step 2: Implement `UpdateClassificationSystems`** at the end of `ClassificationCommands.cpp`. The date parse reuses the `#ifdef ServerMainVers_2900` split already used by `CreateClassificationSystemsCommand::Execute`:

```cpp
UpdateClassificationSystemsCommand::UpdateClassificationSystemsCommand () :
    CommandBase (CommonSchema::Used)
{}

GS::String UpdateClassificationSystemsCommand::GetName () const
{
    return "UpdateClassificationSystems";
}

GS::Optional<GS::UniString> UpdateClassificationSystemsCommand::GetInputParametersSchema () const
{
    return R"({
        "type": "object",
        "properties": {
            "classificationSystems": {
                "type": "array",
                "description": "The classification systems to update. Only the fields given change.",
                "items": {
                    "type": "object",
                    "properties": {
                        "classificationSystemId": { "$ref": "#/ClassificationSystemId" },
                        "name": { "type": "string" },
                        "description": { "type": "string" },
                        "source": { "type": "string" },
                        "version": { "type": "string" },
                        "date": { "$ref": "#/Date" }
                    },
                    "additionalProperties": false,
                    "required": [ "classificationSystemId" ]
                }
            }
        },
        "additionalProperties": false,
        "required": [ "classificationSystems" ]
    })";
}

GS::Optional<GS::UniString> UpdateClassificationSystemsCommand::GetRawResponseSchema () const
{
    return R"({
        "type": "object",
        "properties": { "executionResults": { "$ref": "#/ExecutionResults" } },
        "additionalProperties": false,
        "required": [ "executionResults" ]
    })";
}

GS::ObjectState UpdateClassificationSystemsCommand::Execute (const GS::ObjectState& parameters, GS::ProcessControl& /*processControl*/) const
{
    GS::Array<GS::ObjectState> systems;
    parameters.Get ("classificationSystems", systems);

    GS::ObjectState response;
    const auto& executionResults = response.AddList<GS::ObjectState> ("executionResults");

    ACAPI_CallUndoableCommand ("UpdateClassificationSystems", [&]() -> GSErrCode {
        for (const GS::ObjectState& item : systems) {
            const GS::ObjectState* systemId = item.Get ("classificationSystemId");
            if (systemId == nullptr) {
                executionResults (CreateFailedExecutionResult (APIERR_BADPARS, "classificationSystemId is missing"));
                continue;
            }
            API_ClassificationSystem system;
            system.guid = GetGuidFromObjectState (*systemId);
            if (ACAPI_Classification_GetClassificationSystem (system) != NoError) {
                executionResults (CreateFailedExecutionResult (APIERR_BADID, "classification system not found"));
                continue;
            }
            item.Get ("name", system.name);
            item.Get ("description", system.description);
            item.Get ("source", system.source);
            item.Get ("version", system.editionVersion);
            GS::UniString date;
            if (item.Get ("date", date)) {
                unsigned int year = 0, month = 0, day = 0;
                if (date.SScanf ("%4u-%2u-%2u", &year, &month, &day) != 3) {
                    executionResults (CreateFailedExecutionResult (APIERR_BADPARS, "date must be YYYY-MM-DD"));
                    continue;
                }
#ifdef ServerMainVers_2900
                system.editionDate = std::chrono::year_month_day (std::chrono::year (year), std::chrono::month (month), std::chrono::day (day));
#else
                system.editionDate = GSDateRecord ((unsigned short) year, (unsigned short) month, (unsigned short) day);
#endif
            }
            const GSErrCode err = ACAPI_Classification_ChangeClassificationSystem (system);
            if (err != NoError) {
                executionResults (CreateFailedExecutionResult (err, DescribeDefinitionChangeError (err)));
                continue;
            }
            executionResults (CreateSuccessfulExecutionResult ());
        }
        return NoError;
    });

    return response;
}
```

- [ ] **Step 3: Implement `UpdateClassificationItems`** the same way: name `"UpdateClassificationItems"`, input array `classificationItems` of `{classificationItemId: {"$ref":"#/ClassificationItemId"}, id: string, name: string, description: string}` with `required: ["classificationItemId"]`, same response schema. Execute body per item:

```cpp
            const GS::ObjectState* itemId = item.Get ("classificationItemId");
            if (itemId == nullptr) {
                executionResults (CreateFailedExecutionResult (APIERR_BADPARS, "classificationItemId is missing"));
                continue;
            }
            API_ClassificationItem classificationItem;
            classificationItem.guid = GetGuidFromObjectState (*itemId);
            if (ACAPI_Classification_GetClassificationItem (classificationItem) != NoError) {
                executionResults (CreateFailedExecutionResult (APIERR_BADID, "classification item not found"));
                continue;
            }
            item.Get ("id", classificationItem.id);
            item.Get ("name", classificationItem.name);
            item.Get ("description", classificationItem.description);
            const GSErrCode err = ACAPI_Classification_ChangeClassificationItem (classificationItem);
            if (err != NoError) {
                executionResults (CreateFailedExecutionResult (err, DescribeDefinitionChangeError (err)));
                continue;
            }
            executionResults (CreateSuccessfulExecutionResult ());
```

wrapped in `ACAPI_CallUndoableCommand ("UpdateClassificationItems", ...)` exactly like Step 2.

- [ ] **Step 4: Register** in `AddOnMain.cpp` after `DeleteClassificationItemsCommand`:

```cpp
        err |= RegisterCommand<UpdateClassificationSystemsCommand> (
            classificationCommands, "1.5.9",
            "Updates the name, description, source, version and/or date of existing Classification Systems, keeping their guid."
        );
        err |= RegisterCommand<UpdateClassificationItemsCommand> (
            classificationCommands, "1.5.9",
            "Updates the id (code), name and/or description of existing Classification Items, keeping their guid and so the elements classified with them. Items cannot be moved to another parent."
        );
```

- [ ] **Step 5: Example** `archicad-addon/Examples/update_classifications.py`:

```python
import aclib

# Creates a small classification system, renames the system and changes an item's code,
# then deletes it. The item keeps its identifier, which is what keeps elements
# classified with it and properties available for it.

SYSTEM = 'Tapir Example Update System'

aclib.RunTapirCommand ('CreateClassificationSystems', {'classificationSystemsWithItems': [{
    'classificationSystem': {'name': SYSTEM, 'description': '', 'source': '', 'version': '1', 'date': '2026-01-01'},
    'classificationItems': [{'id': '10', 'name': 'Walls', 'description': ''}]}]}, debug = False)


def FindSystem (name):
    for s in aclib.RunCommand ('API.GetAllClassificationSystems', {})['classificationSystems']:
        if s['name'] == name:
            return s
    return None


system = FindSystem (SYSTEM)
items = aclib.RunCommand ('API.GetAllClassificationsInSystem', {'classificationSystemId': system['classificationSystemId']})
item = items['classificationItems'][0]['classificationItem']

r1 = aclib.RunTapirCommand ('UpdateClassificationSystems', {'classificationSystems': [{
    'classificationSystemId': system['classificationSystemId'], 'name': SYSTEM + ' Renamed', 'version': '2', 'date': '2026-09-25'}]}, debug = False)
r2 = aclib.RunTapirCommand ('UpdateClassificationItems', {'classificationItems': [{
    'classificationItemId': item['classificationItemId'], 'id': '11', 'name': 'Walls, external'}]}, debug = False)
print ('System update succeeded: {}'.format (r1['executionResults'][0]['success']))
print ('Item update succeeded: {}'.format (r2['executionResults'][0]['success']))

renamed = FindSystem (SYSTEM + ' Renamed')
after = aclib.RunCommand ('API.GetAllClassificationsInSystem', {'classificationSystemId': renamed['classificationSystemId']})
afterItem = after['classificationItems'][0]['classificationItem']
print ('Same system guid: {}'.format (renamed['classificationSystemId'] == system['classificationSystemId']))
print ('Item now: {} {}'.format (afterItem['id'], afterItem['name']))
print ('Same item guid: {}'.format (afterItem['classificationItemId'] == item['classificationItemId']))

aclib.RunTapirCommand ('DeleteClassificationSystems', {'classificationSystemIds': [
    {'classificationSystemId': renamed['classificationSystemId']}]}, debug = False)
```

Expected output (Task 7): `True`, `True`, `Same system guid: True`, `Item now: 11 Walls, external`, `Same item guid: True`.

- [ ] **Step 6: Compile check** as in Task 3 Step 5.

- [ ] **Step 7: Commit**

```bash
git add archicad-addon/Sources/ClassificationCommands.hpp archicad-addon/Sources/ClassificationCommands.cpp archicad-addon/Sources/AddOnMain.cpp archicad-addon/Examples/update_classifications.py
git commit -m "feat: UpdateClassificationSystems and UpdateClassificationItems edit in place

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `ImportPropertiesXml` and `ImportClassificationsXml`

**Files:**
- Modify: `archicad-addon/Sources/PropertyCommands.hpp/.cpp`, `ClassificationCommands.hpp/.cpp`, `AddOnMain.cpp`
- Create: `archicad-addon/Examples/import_definitions_xml.py`

**Interfaces:**
- Consumes: `DescribeDefinitionChangeError` (Task 2); Task 1 Step 4 note (XML dialect).
- Produces (JSON): `ImportPropertiesXml` input `{"xml": string, "conflictPolicy": "append"|"replace"|"skip"}`; `ImportClassificationsXml` input `{"xml": string, "systemConflictPolicy": "merge"|"replace"|"skip", "itemConflictPolicy": "replace"|"skip"}`. Both respond `{"executionResult": ExecutionResult, "created": [{"guid"}], "removed": [{"guid"}]}`. For properties the guids are property definition guids; for classifications they are classification item guids plus system guids.

- [ ] **Step 1: Snapshot helpers.** In `PropertyCommands.cpp`:

```cpp
// The import calls return only an error code. What they did is read off the
// definitions before and after: a guid-preserving replace is in neither list.
static GS::HashSet<API_Guid> AllCustomPropertyGuids ()
{
    GS::HashSet<API_Guid> guids;
    GS::Array<API_PropertyGroup> groups;
    ACAPI_Property_GetPropertyGroups (groups);
    for (const API_PropertyGroup& group : groups) {
        GS::Array<API_PropertyDefinition> definitions;
        ACAPI_Property_GetPropertyDefinitions (group.guid, definitions);
        for (const API_PropertyDefinition& d : definitions) {
            if (d.definitionType == API_PropertyCustomDefinitionType) {
                guids.Add (d.guid);
            }
        }
    }
    return guids;
}

static void AddGuidDifference (const GS::HashSet<API_Guid>& from, const GS::HashSet<API_Guid>& minus, const GS::ObjectState::ListInserter<GS::ObjectState>& list)
{
    for (const API_Guid& guid : from) {
        if (!minus.Contains (guid)) {
            list (CreateGuidObjectState (guid));
        }
    }
}
```

If `GS::ObjectState::ListInserter` is not the name of the type `AddList` returns in this GS version, take the list by `const auto&` in a template: `template <typename List> static void AddGuidDifference (..., const List& list)`. Add `#include "HashSet.hpp"` if not already included.

In `ClassificationCommands.cpp`, the matching `AllClassificationGuids ()` walks `ACAPI_Classification_GetClassificationSystems`, then `ACAPI_Classification_GetClassificationSystemRootItems` and recursively `ACAPI_Classification_GetClassificationItemChildren`, adding system and item guids to one set.

- [ ] **Step 2: `ImportPropertiesXmlCommand`** (declare in `.hpp` as in Task 3 Step 1). Input schema:

```json
{
    "type": "object",
    "properties": {
        "xml": { "type": "string", "description": "A Property Manager export (XML) to import." },
        "conflictPolicy": { "type": "string", "enum": [ "append", "replace", "skip" ], "description": "What to do with a property whose name already exists in its group: append imports it under a new unused name, replace replaces the existing definition, skip keeps the existing one." }
    },
    "additionalProperties": false,
    "required": [ "xml", "conflictPolicy" ]
}
```

Response schema:

```json
{
    "type": "object",
    "properties": {
        "executionResult": { "$ref": "#/ExecutionResult" },
        "created": { "type": "array", "items": { "$ref": "#/PropertyId" } },
        "removed": { "type": "array", "items": { "$ref": "#/PropertyId" } }
    },
    "additionalProperties": false,
    "required": [ "executionResult", "created", "removed" ]
}
```

Execute:

```cpp
GS::ObjectState ImportPropertiesXmlCommand::Execute (const GS::ObjectState& parameters, GS::ProcessControl& /*processControl*/) const
{
    GS::UniString xml, policyStr;
    parameters.Get ("xml", xml);
    parameters.Get ("conflictPolicy", policyStr);
    const API_PropertyDefinitionNameConflictResolutionPolicy policy =
        policyStr == "replace" ? API_ReplaceConflictingProperties :
        policyStr == "skip"    ? API_SkipConflictingProperties :
                                 API_AppendConflictingProperties;

    GS::ObjectState response;
    const GS::HashSet<API_Guid> before = AllCustomPropertyGuids ();
    GSErrCode err = NoError;
    ACAPI_CallUndoableCommand ("ImportPropertiesXml", [&]() -> GSErrCode {
        err = ACAPI_Property_Import (xml, policy);
        return err;
    });
    const GS::HashSet<API_Guid> after = AllCustomPropertyGuids ();

    response.Add ("executionResult", err == NoError ? CreateSuccessfulExecutionResult ()
                                                    : CreateFailedExecutionResult (err, err == APIERR_BADPARS ? "invalid property XML" : DescribeDefinitionChangeError (err)));
    AddGuidDifference (after, before, response.AddList<GS::ObjectState> ("created"));
    AddGuidDifference (before, after, response.AddList<GS::ObjectState> ("removed"));
    return response;
}
```

`created` / `removed` entries are `{"guid": ...}`, matching `PropertyId`.

- [ ] **Step 3: `ImportClassificationsXmlCommand`**, same pattern: input `xml`, `systemConflictPolicy` (`merge|replace|skip` mapping to `API_MergeConflictingSystems|API_ReplaceConflictingSystems|API_SkipConflictingSystems`), `itemConflictPolicy` (`replace|skip` mapping to `API_ReplaceConflictingItems|API_SkipConflicitingItems`, the DevKit's spelling), call `ACAPI_Classification_Import (xml, systemPolicy, itemPolicy)` inside `ACAPI_CallUndoableCommand ("ImportClassificationsXml", ...)`, before / after with `AllClassificationGuids ()`, same response shape with `created` / `removed` items as `{"guid"}` (schema `items: {"type":"object","properties":{"guid":{"$ref":"#/Guid"}},"required":["guid"],"additionalProperties":false}`), and `"invalid classification XML"` for `APIERR_BADPARS`.

- [ ] **Step 4: Register** under the property and classification groups:

```cpp
        err |= RegisterCommand<ImportPropertiesXmlCommand> (
            propertyCommands, "1.5.9",
            "Imports a Property Manager XML export, with the given policy for names that already exist. Returns the property definitions it created and removed."
        );
```

```cpp
        err |= RegisterCommand<ImportClassificationsXmlCommand> (
            classificationCommands, "1.5.9",
            "Imports a Classification Manager XML export, with the given policies for systems and items that already exist. Returns the systems and items it created and removed."
        );
```

- [ ] **Step 5: Example** `archicad-addon/Examples/import_definitions_xml.py`. It imports a minimal property XML written in the dialect recorded in Task 1 Step 4 (one group `Tapir Example Import`, one String property `Imported`), prints `executionResult.success` and the number of created ids, then deletes what was created (`DeletePropertyDefinitions` with the created ids, `DeletePropertyGroups` with the group found by name in `GetAllProperties` `propertyGroups`). Build the XML string from the smallest valid subset of the Task 1 export: copy one `PropertyDefinitionGroup` element from `properties.xml`, rename it, keep one definition. Expected output: `Import succeeded: True`, `Created: 1`.

- [ ] **Step 6: Compile check** as in Task 3 Step 5.

- [ ] **Step 7: Commit**

```bash
git add archicad-addon/Sources archicad-addon/Examples/import_definitions_xml.py
git commit -m "feat: ImportPropertiesXml and ImportClassificationsXml report what they created

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `GetAllProperties` reports group id, description, default and availability

**Files:**
- Modify: `archicad-addon/Sources/PropertyCommands.cpp` (`GetAllPropertiesCommand`, lines ~176-250; move `PackTypes` and `GetPropertyValueString` above it)
- Modify: `archicad-addon/Sources/RFIX/Images/CommonSchemaDefinitions.json` (`PropertyDetails`)
- Modify: `archicad-addon/Examples/get_all_properties.py` (print the new fields for one custom property)

**Interfaces:**
- Produces (JSON), additive: per property `propertyGroupId {guid}`, `propertyDescription` (string), `defaultValueDisplay` (string, custom non-expression properties with a normal default only), `availability` (`[{classificationItemId:{guid}}]`, custom only). Top level: `propertyGroups [{propertyGroupId {guid}, name, description, isCustom}]`.

- [ ] **Step 1: Move** `PackTypes` and `static GSErrCode GetPropertyValueString (...)` (currently after `GetAllPropertiesCommand::Execute`) to just above `GetAllPropertiesCommand::GetAllPropertiesCommand`. No other change to them.

- [ ] **Step 2: Extend the schemas.** In `CommonSchemaDefinitions.json`, add to `PropertyDetails.properties`:

```json
"propertyGroupId": { "$ref": "#/PropertyGroupId" },
"propertyDescription": { "type": "string" },
"defaultValueDisplay": { "type": "string", "description": "The basic default value as text. Only for custom, non expression-based properties whose default is set." },
"availability": { "type": "array", "description": "Classification items a custom property is available for.", "items": { "$ref": "#/ClassificationItemIdArrayItem" } }
```

In `GetAllPropertiesCommand::GetRawResponseSchema`, add next to `properties`:

```json
"propertyGroups": {
    "type": "array",
    "description": "Every property group, including empty ones.",
    "items": {
        "type": "object",
        "properties": {
            "propertyGroupId": { "$ref": "#/PropertyGroupId" },
            "name": { "type": "string" },
            "description": { "type": "string" },
            "isCustom": { "type": "boolean" }
        },
        "additionalProperties": false,
        "required": [ "propertyGroupId", "name", "isCustom" ]
    }
}
```

- [ ] **Step 3: Fill them** in `GetAllPropertiesCommand::Execute`. Before the group loop: `const auto& groupList = response.AddList<GS::ObjectState> ("propertyGroups");`. At the top of the group loop body:

```cpp
        GS::ObjectState groupDetails;
        groupDetails.Add ("propertyGroupId", CreateGuidObjectState (group.guid));
        groupDetails.Add ("name", group.name);
        groupDetails.Add ("description", group.description);
        groupDetails.Add ("isCustom", group.groupType == API_PropertyCustomGroupType);
        groupList (groupDetails);
```

Inside the definition loop, after `details.Add ("isExpressionBased", ...)`:

```cpp
            details.Add ("propertyGroupId", CreateGuidObjectState (group.guid));
            details.Add ("propertyDescription", definition.description);
            if (definition.definitionType == API_PropertyCustomDefinitionType) {
                if (!definition.defaultValue.hasExpression &&
                    definition.defaultValue.basicValue.variantStatus == API_VariantStatusNormal) {
                    API_Property defaultProperty;
                    defaultProperty.definition = definition;
                    defaultProperty.isDefault = true;
                    defaultProperty.status = API_Property_HasValue;
                    defaultProperty.value = definition.defaultValue.basicValue;
                    GS::UniString defaultString;
                    if (GetPropertyValueString (defaultProperty, defaultString) == NoError) {
                        details.Add ("defaultValueDisplay", defaultString);
                    }
                }
                const auto& availabilityList = details.AddList<GS::ObjectState> ("availability");
                for (const API_Guid& itemGuid : definition.availability) {
                    availabilityList (CreateIdObjectState ("classificationItemId", itemGuid));
                }
            }
```

This reads definitions only; no element value is touched.

- [ ] **Step 4: Example.** Append to `Examples/get_all_properties.py`:

```python
custom = [p for p in properties if p['propertyType'] == 'Custom']
if custom:
    p = custom[0]
    print ('First custom property: {}/{}'.format (p['propertyGroupName'], p['propertyName']))
    print ('  has group id: {}'.format ('propertyGroupId' in p))
    print ('  availability entries: {}'.format (len (p.get ('availability', []))))
```

(match the variable name the example already uses for the property list).

- [ ] **Step 5: Compile check**, then **commit**:

```bash
git add archicad-addon/Sources/PropertyCommands.cpp archicad-addon/Sources/RFIX/Images/CommonSchemaDefinitions.json archicad-addon/Examples/get_all_properties.py
git commit -m "feat: GetAllProperties reports group id, description, default and availability

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Integration build, install, live example run, fixture capture

**Files:**
- Create (branch only): `local/integration-2026-09` in the Tapir clone
- Create: `tests/fixtures/definition_edit_replays.py` in the MCP repo (recorded responses, trimmed)

- [ ] **Step 1: Integration branch and build**

```bash
cd /Users/alesd/Developer/tapir-archicad-automation
git switch -c local/integration-2026-09 feature/section-dimension-chains
git merge --no-ff feature/property-classification-editing -m "merge: property and classification editing into the local build"
cmake -B archicad-addon/Build/AC29 -G Xcode -DAC_VERSION=29 -DAC_API_DEVKIT_DIR=archicad-addon/Build/DevKits/AC29/Support archicad-addon
cmake --build archicad-addon/Build/AC29 --config RelWithDebInfo 2>&1 | tail -3
```

Expected: `** BUILD SUCCEEDED **`. Merge conflicts in `AddOnMain.cpp` are registration lists: keep both sides' lines.

- [ ] **Step 2: Install (confirm with Aleš first; it needs every Archicad closed).**

```bash
cp -R "/Applications/Graphisoft/Archicad 29/Add-Ons/TapirAddOn_AC29_Mac.bundle" ~/Downloads/TapirAddOn_AC29_Mac.bundle.backup-1.5.9-20260925
rm -rf "/Applications/Graphisoft/Archicad 29/Add-Ons/TapirAddOn_AC29_Mac.bundle"
cp -R archicad-addon/Build/AC29/RelWithDebInfo/TapirAddOn_AC29_Mac.bundle "/Applications/Graphisoft/Archicad 29/Add-Ons/"
```

Aleš relaunches Archicad with Oprema-objekti only. Wait for the project to load (add-on commands time out for a minute or two after launch).

- [ ] **Step 3: Run every new or changed example against 19724**

```bash
cd archicad-addon/Examples
for f in update_property_definitions update_property_groups update_classifications import_definitions_xml get_all_properties create_section_dimension_chains; do echo "== $f"; python3 $f.py --port 19724 | tail -8; done
```

Expected: the outputs listed in Tasks 2, 3 (now `True` twice), 4 and 5; `create_section_dimension_chains` still runs (proves the merged build kept it). Any `False` stops the plan: fix in the feature branch, re-merge, rebuild, reinstall.

- [ ] **Step 4: Settle the two open questions on 19724**
  1. Create a throwaway singleEnum property available for the "MCP Test" Wall item, set it to option `X` on one test wall via `set_element_data`-free Tapir `SetPropertyValuesOfElements`, remove option `X` with `UpdatePropertyDefinitions`, and ask Aleš to look at that wall's property in the Info Box (no API value read). Record what it shows.
  2. Import `exports/properties.xml` with `conflictPolicy: replace` and compare the "MCP Test/Fire Rating" guid before and after via `GetAllProperties` (definition read only). Record whether it changed.

Write both answers into Task 14's known-issues text.

- [ ] **Step 5: Record fixtures.** Save real responses of `GetAllProperties` (trimmed to the "MCP Test" group plus one built-in), `API.GetAllClassificationSystems` and `API.GetAllClassificationsInSystem` (trimmed to the "MCP Test" system), one success and one failure `executionResults` from each Update command, and one import response, into `tests/fixtures/definition_edit_replays.py` in the MCP repo as Python dict literals named `LIVE_GET_ALL_PROPERTIES`, `LIVE_CLASSIFICATION_SYSTEMS`, `LIVE_CLASSIFICATION_TREE`, `LIVE_UPDATE_OK`, `LIVE_UPDATE_FAILED`, `LIVE_IMPORT_RESPONSE`. Replace any guid that is not from the test groups with a placeholder string. Commit it in Task 8.

---

## Phase B: MCP (Python)

All commands run from `/Users/alesd/Developer/Archicad MCP`.

### Task 8: Branch, fixtures, classification index

**Files:**
- Create: `src/archicad_mcp/core/definition_edit.py` (shared part)
- Create: `tests/fixtures/definition_edit_replays.py` (from Task 7 Step 5, plus synthetic cases below)
- Test: `tests/test_definition_edit.py`

**Interfaces:**
- Produces: `EDIT_MARKER: str = "UpdateClassificationItems"`, `editing_unavailable(conn) -> dict | None`, `class ClassificationIndex` with `load(conn) -> ClassificationIndex`, `.systems: dict[str, ClassSystem]`, `.items: dict[str, ClassItem]`, `.resolve(address: str) -> tuple[list[str], str | None]`, `.label(guid: str) -> str`, `.descendants(guid: str) -> list[str]`, `.siblings(guid: str) -> list[str]`; dataclasses `ClassItem(guid, code, name, description, system, parent, children)`, `ClassSystem(guid, name, description, source, version, date, roots)`; `group_by_error(failed: list[dict]) -> list[dict]`.

- [ ] **Step 1: Branch**

```bash
git switch -c feature/definition-editing main
```

- [ ] **Step 2: Synthetic fixtures.** Append to `tests/fixtures/definition_edit_replays.py` (below the live ones):

```python
"""Definition editing fixtures.

The LIVE_* dicts are responses recorded on Oprema-objekti (AC 29, the
property-classification-editing Tapir build), trimmed to the MCP Test group and
system. The synthetic ones below cover the awkward cases no test model has.
"""

# Two systems where one name is a prefix of the other, a group with a slash in its
# name, an enum with a duplicated option text.
SYSTEMS = {"classificationSystems": [
    {"classificationSystemId": {"guid": "sys-elea"}, "name": "ELEA",
     "description": "", "source": "", "version": "1", "date": "2026-01-01"},
    {"classificationSystemId": {"guid": "sys-elea2"}, "name": "ELEA 2",
     "description": "", "source": "", "version": "1", "date": "2026-01-01"},
]}

TREES = {
    "sys-elea": {"classificationItems": [
        {"classificationItem": {"classificationItemId": {"guid": "i-40"}, "id": "40",
                                "name": "Oprema", "description": "", "children": [
            {"classificationItem": {"classificationItemId": {"guid": "i-40-10"}, "id": "40.10",
                                    "name": "Kuhinja", "description": ""}},
            {"classificationItem": {"classificationItemId": {"guid": "i-40-20"}, "id": "40.20",
                                    "name": "Sanitarije", "description": "", "children": [
                {"classificationItem": {"classificationItemId": {"guid": "i-40-20-1"},
                                        "id": "40.20.1", "name": "WC", "description": ""}}]}},
        ]}},
    ]},
    "sys-elea2": {"classificationItems": [
        {"classificationItem": {"classificationItemId": {"guid": "j-40"}, "id": "40",
                                "name": "Other", "description": ""}},
    ]},
}


def _custom(guid, group, name, collection="Single", value="String", measure="Default",
            enum=None, availability=(), default=None, expressions=None,
            group_guid=None, description=""):
    p = {"propertyId": {"guid": guid}, "propertyType": "Custom",
         "propertyGroupName": group, "propertyName": name,
         "propertyCollectionType": collection, "propertyValueType": value,
         "propertyMeasureType": measure, "propertyIsEditable": True,
         "isExpressionBased": expressions is not None,
         "propertyGroupId": {"guid": group_guid or f"g-{group}"},
         "propertyDescription": description,
         "availability": [{"classificationItemId": {"guid": g}} for g in availability]}
    if enum is not None:
        p["possibleEnumValues"] = [{"enumValue": {"displayValue": d, "guid": g}} for g, d in enum]
    if default is not None:
        p["defaultValueDisplay"] = default
    if expressions is not None:
        p["expressions"] = expressions
    return p


ALL_PROPERTIES = {
    "properties": [
        _custom("p-code", "ELEA", "Sifra", default="/", availability=["i-40", "i-40-10"]),
        _custom("p-cat", "ELEA", "Kategorija", collection="SingleChoiceEnumeration",
                enum=[("e-k", "Kuhinja"), ("e-s", "Sanitarije"), ("e-o", "Staro")],
                default="Kuhinja", availability=["i-40"]),
        _custom("p-dup", "ELEA", "Dvojnik", collection="SingleChoiceEnumeration",
                enum=[("e-a", "A"), ("e-a2", "A"), ("e-b", "B")], default="B"),
        _custom("p-len", "ELEA", "Dolzina", value="Real", measure="Length", default="0.00"),
        _custom("p-expr", "ELEA", "Povrsina", value="Real", expressions=["{Property:Area}"]),
        _custom("p-slash", "A/B", "C", group_guid="g-slash"),
        {"propertyId": {"guid": "b-layer"}, "propertyType": "StaticBuiltIn",
         "propertyGroupName": "Model View", "propertyName": "Layer Name",
         "propertyCollectionType": "Single", "propertyValueType": "String",
         "propertyMeasureType": "Default", "propertyIsEditable": True,
         "isExpressionBased": False, "propertyGroupId": {"guid": "g-mv"},
         "propertyDescription": ""},
    ],
    "propertyGroups": [
        {"propertyGroupId": {"guid": "g-ELEA"}, "name": "ELEA", "description": "", "isCustom": True},
        {"propertyGroupId": {"guid": "g-ELEA Oprema"}, "name": "ELEA Oprema", "description": "", "isCustom": True},
        {"propertyGroupId": {"guid": "g-slash"}, "name": "A/B", "description": "", "isCustom": True},
        {"propertyGroupId": {"guid": "g-mv"}, "name": "Model View", "description": "", "isCustom": False},
    ],
}


def fake_editing_core(update=None, extra_tapir=None, marker=True):
    """A FakeCore wired with the synthetic definitions. `update` answers every
    Update* / Import* Tapir command (default: all succeed)."""
    from tests.conftest import FakeCore

    def ok(params):
        key = next(iter(params))
        return {"executionResults": [{"success": True} for _ in params[key]]}

    official = {
        "API.IsAddOnCommandAvailable": lambda p: {"available": marker or
            p["addOnCommandId"]["commandName"] != "UpdateClassificationItems"},
        "API.GetAllClassificationSystems": SYSTEMS,
        "API.GetAllClassificationsInSystem":
            lambda p: TREES[p["classificationSystemId"]["guid"]],
    }
    tapir = {"GetAllProperties": ALL_PROPERTIES,
             "UpdatePropertyDefinitions": update or ok,
             "UpdatePropertyGroups": update or ok,
             "UpdateClassificationSystems": update or ok,
             "UpdateClassificationItems": update or ok}
    tapir.update(extra_tapir or {})
    return FakeCore(official=official, tapir=tapir)
```

The `API.IsAddOnCommandAvailable` lambda reads `addOnCommandId.commandName`, the shape `_tapir_probe` in `src/archicad_mcp/connection.py` builds.

- [ ] **Step 3: Write the failing tests** in `tests/test_definition_edit.py`:

```python
"""Definition editing: address resolution, plans, sending. No property VALUE is
ever read here; the fake core has no GetPropertyValuesOfElements on purpose."""
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.definition_edit import (
    ClassificationIndex, editing_unavailable, group_by_error)
from tests.fixtures.definition_edit_replays import fake_editing_core


def _conn(**kw):
    core = fake_editing_core(**kw)
    return ArchicadConnection(19724, core=core), core


def test_resolve_item_by_system_and_code():
    index = ClassificationIndex.load(_conn()[0])
    assert index.resolve("ELEA/40.10") == (["i-40-10"], None)


def test_branch_suffix_takes_the_item_and_everything_below():
    index = ClassificationIndex.load(_conn()[0])
    guids, err = index.resolve("ELEA/40.20/*")
    assert err is None
    assert guids == ["i-40-20", "i-40-20-1"]


def test_longest_system_name_wins():
    index = ClassificationIndex.load(_conn()[0])
    assert index.resolve("ELEA 2/40") == (["j-40"], None)
    assert index.resolve("ELEA/40") == (["i-40"], None)


def test_unknown_code_is_an_error_naming_the_address():
    index = ClassificationIndex.load(_conn()[0])
    guids, err = index.resolve("ELEA/99")
    assert guids == [] and "ELEA/99" in err


def test_a_guid_resolves_to_itself():
    index = ClassificationIndex.load(_conn()[0])
    assert index.resolve("i-40") == (["i-40"], None)


def test_label_and_siblings():
    index = ClassificationIndex.load(_conn()[0])
    assert index.label("i-40-20-1") == "ELEA/40.20.1"
    assert index.siblings("i-40-10") == ["i-40-20"]


def test_old_tapir_is_refused_before_anything_is_sent():
    conn, core = _conn(marker=False)
    result = editing_unavailable(conn)
    assert result is not None and "UpdateClassificationItems" in result["error"]
    assert not any(cmd.startswith("Update") for cmd, _ in core.calls)


def test_failures_group_by_message():
    failed = [{"target": "ELEA/Sifra", "message": "no access right"},
              {"target": "ELEA/Kategorija", "message": "no access right"},
              {"target": "ELEA/Dolzina", "message": "name already used"}]
    groups = group_by_error(failed)
    assert groups[0] == {"message": "no access right", "count": 2,
                         "sample": ["ELEA/Sifra", "ELEA/Kategorija"]}
```

- [ ] **Step 4: Run to see them fail**

Run: `uv run pytest tests/test_definition_edit.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'archicad_mcp.core.definition_edit'`.

- [ ] **Step 5: Implement the shared part** of `src/archicad_mcp/core/definition_edit.py`:

```python
"""Editing property and classification DEFINITIONS in place.

Definition reads and Tapir Update* commands only. Nothing here calls
GetPropertyValuesOfElements, so none of it can trigger the Archicad 29
property-read crash (docs/known-issues.md).

Every edit keeps GUIDs: the Tapir commands read the definition, patch only the
fields sent and call ACAPI_*_Change*. That is what keeps element values,
classifications and availability links attached through a rename.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from archicad_mcp.connection import ArchicadConnection

EDIT_MARKER = "UpdateClassificationItems"
FAILURE_SAMPLE = 5

NEEDS_BUILD = (
    "Editing definitions needs a Tapir add-on that has UpdateClassificationItems: "
    "the property-classification-editing build, or the upstream Tapir release that "
    "ships it. The add-on installed in this Archicad does not have it, so nothing "
    "was sent. Older Tapir versions accept UpdatePropertyDefinitions but ignore the "
    "new fields, which is why this is checked first.")


def editing_unavailable(conn: ArchicadConnection) -> dict | None:
    """An error result when the running Tapir lacks the editing commands."""
    if conn.tapir_command_available(EDIT_MARKER):
        return None
    return {"error": NEEDS_BUILD}


def group_by_error(failed: list[dict]) -> list[dict]:
    """Refusals grouped by message, largest group first, a few targets each."""
    groups: dict[str, dict] = {}
    for f in failed:
        g = groups.setdefault(f["message"], {"message": f["message"], "count": 0, "sample": []})
        g["count"] += 1
        if len(g["sample"]) < FAILURE_SAMPLE:
            g["sample"].append(f["target"])
    return sorted(groups.values(), key=lambda g: -g["count"])


def split_results(targets: list[str], response: dict) -> tuple[list[str], list[dict]]:
    """(applied targets, failed [{target, message}]) from an executionResults reply."""
    applied, failed = [], []
    for target, r in zip(targets, response.get("executionResults", [])):
        if r.get("success"):
            applied.append(target)
        else:
            failed.append({"target": target,
                           "message": r.get("error", {}).get("message", "refused")})
    return applied, failed


# ---------- classifications ----------

@dataclass
class ClassItem:
    guid: str
    code: str
    name: str
    description: str
    system: str
    parent: str | None
    children: list[str] = field(default_factory=list)


@dataclass
class ClassSystem:
    guid: str
    name: str
    description: str
    source: str
    version: str
    date: str
    roots: list[str] = field(default_factory=list)


class ClassificationIndex:
    """Every classification system and item of the open project, by name and code."""

    def __init__(self) -> None:
        self.systems: dict[str, ClassSystem] = {}
        self.items: dict[str, ClassItem] = {}

    @classmethod
    def load(cls, conn: ArchicadConnection) -> "ClassificationIndex":
        index = cls()
        response = conn.official("API.GetAllClassificationSystems")
        for s in response.get("classificationSystems", []):
            system = ClassSystem(
                guid=s["classificationSystemId"]["guid"], name=s.get("name", ""),
                description=s.get("description", ""), source=s.get("source", ""),
                version=s.get("version", ""), date=s.get("date", ""))
            index.systems[system.name] = system
            tree = conn.official("API.GetAllClassificationsInSystem",
                                 {"classificationSystemId": s["classificationSystemId"]})
            system.roots = index._walk(tree.get("classificationItems", []), system.name, None)
        return index

    def _walk(self, wrappers: list[dict], system: str, parent: str | None) -> list[str]:
        guids = []
        for wrapper in wrappers:
            item = wrapper.get("classificationItem", wrapper)
            guid = item.get("classificationItemId", {}).get("guid")
            if not guid:
                continue
            node = ClassItem(guid, item.get("id", ""), item.get("name", ""),
                             item.get("description", ""), system, parent)
            self.items[guid] = node
            node.children = self._walk(item.get("children", []), system, guid)
            guids.append(guid)
        return guids

    def system_of_guid(self, guid: str) -> ClassSystem | None:
        return next((s for s in self.systems.values() if s.guid == guid), None)

    def label(self, guid: str) -> str:
        item = self.items.get(guid)
        return f"{item.system}/{item.code}" if item else guid

    def descendants(self, guid: str) -> list[str]:
        out: list[str] = []
        for child in self.items[guid].children:
            out.append(child)
            out.extend(self.descendants(child))
        return out

    def siblings(self, guid: str) -> list[str]:
        item = self.items[guid]
        pool = (self.items[item.parent].children if item.parent
                else self.systems[item.system].roots)
        return [g for g in pool if g != guid]

    def _by_code(self, ref: str) -> tuple[str | None, str | None]:
        # Longest system name first: "ELEA 2/40" must not match system "ELEA".
        for name in sorted(self.systems, key=len, reverse=True):
            if ref.startswith(name + "/"):
                code = ref[len(name) + 1:]
                matches = [g for g, i in self.items.items()
                           if i.system == name and i.code == code]
                if len(matches) == 1:
                    return matches[0], None
                if matches:
                    return None, f"'{ref}' matches {len(matches)} items; address it by GUID"
                return None, f"no classification item '{ref}' (code '{code}' in system '{name}')"
        known = ", ".join(sorted(self.systems)) or "none"
        return None, f"no classification item '{ref}': write System/Code; systems here: {known}"

    def resolve(self, address: str) -> tuple[list[str], str | None]:
        """'System/Code' -> [guid]; 'System/Code/*' -> that item and all below it;
        an item GUID -> [guid]."""
        branch = address.endswith("/*")
        ref = address[:-2] if branch else address
        if ref in self.items:
            guid, err = ref, None
        else:
            guid, err = self._by_code(ref)
        if guid is None:
            return [], err
        return ([guid, *self.descendants(guid)] if branch else [guid]), None
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_definition_edit.py -v`
Expected: all 8 PASS.

- [ ] **Step 7: Commit**

```bash
git add src/archicad_mcp/core/definition_edit.py tests/test_definition_edit.py tests/fixtures/definition_edit_replays.py
git commit -m "feat: classification index and capability gate for definition editing

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Property definitions index and the non-enum part of the plan

**Files:**
- Modify: `src/archicad_mcp/core/definition_edit.py`
- Test: `tests/test_definition_edit.py`

**Interfaces:**
- Consumes: `ClassificationIndex`, `value_fit` from `archicad_mcp.core.element_data`.
- Produces: `@dataclass PropDef` (fields `guid, group, name, builtin, collection, value_type, measure, expressions, enum: list[tuple[str, str]]` as (guid, display), `description, group_guid, default, availability: list[str]`; properties `address`, `type_key`); `class Definitions` with `load(conn)`, `.by_address`, `.by_guid`, `.groups: dict[str, str]` (custom group name to guid), `.resolve(ref) -> tuple[PropDef | None, str | None]`; `plan_property_change(change: dict, defs: Definitions, index: ClassificationIndex | None) -> PlannedEdit`; `@dataclass PlannedEdit(target: str, payload: dict, changes: dict, warnings: list[str], errors: list[str])`.

- [ ] **Step 1: Failing tests** (append to `tests/test_definition_edit.py`):

```python
from archicad_mcp.core.definition_edit import Definitions, plan_property_change


def _defs():
    conn, _ = _conn()
    return Definitions.load(conn), ClassificationIndex.load(conn)


def test_resolve_property_whose_group_contains_a_slash():
    defs, _ = _defs()
    p, err = defs.resolve("A/B/C")
    assert err is None and p.guid == "p-slash"


def test_builtin_properties_are_refused():
    defs, index = _defs()
    plan = plan_property_change({"property": "b-layer", "name": "X"}, defs, index)
    assert plan.errors == ["built-in properties cannot be edited"]


def test_rename_and_move_group():
    defs, index = _defs()
    plan = plan_property_change({"property": "ELEA/Sifra", "name": "Sifra opreme",
                                 "group": "ELEA Oprema"}, defs, index)
    assert plan.errors == []
    assert plan.payload == {"propertyId": {"guid": "p-code"}, "name": "Sifra opreme",
                            "groupId": {"guid": "g-ELEA Oprema"}}
    assert plan.changes == {"name": ["Sifra", "Sifra opreme"], "group": ["ELEA", "ELEA Oprema"]}


def test_rename_onto_an_existing_address_is_an_error():
    defs, index = _defs()
    plan = plan_property_change({"property": "ELEA/Sifra", "name": "Kategorija"}, defs, index)
    assert plan.errors == ["'ELEA/Kategorija' already exists"]


def test_missing_group_is_an_error_not_a_create():
    defs, index = _defs()
    plan = plan_property_change({"property": "ELEA/Sifra", "group": "Nope"}, defs, index)
    assert "no custom property group 'Nope'" in plan.errors[0]


def test_unknown_field_is_an_error():
    defs, index = _defs()
    plan = plan_property_change({"property": "ELEA/Sifra", "colour": "red"}, defs, index)
    assert "unknown field" in plan.errors[0]


def test_plain_default_is_type_checked():
    defs, index = _defs()
    ok = plan_property_change({"property": "ELEA/Dolzina", "default": 2.5}, defs, index)
    assert ok.payload["defaultValue"] == {"basicDefaultValue": {
        "status": "normal", "type": "length", "value": 2.5}}
    bad = plan_property_change({"property": "ELEA/Dolzina", "default": "long"}, defs, index)
    assert "takes a number" in bad.errors[0]


def test_default_none_means_undefined():
    defs, index = _defs()
    plan = plan_property_change({"property": "ELEA/Sifra", "default": None}, defs, index)
    assert plan.payload["defaultValue"] == {"basicDefaultValue": {"status": "userUndefined"}}


def test_expressions_replace_the_default():
    defs, index = _defs()
    plan = plan_property_change({"property": "ELEA/Povrsina",
                                 "expressions": ["{Property:Volume}"]}, defs, index)
    assert plan.payload["defaultValue"] == {"expressions": ["{Property:Volume}"]}
    assert plan.changes["default"] == [{"expressions": ["{Property:Area}"]},
                                       {"expressions": ["{Property:Volume}"]}]


def test_default_and_expressions_together_is_an_error():
    defs, index = _defs()
    plan = plan_property_change({"property": "ELEA/Povrsina", "default": 1,
                                 "expressions": ["1"]}, defs, index)
    assert plan.errors == ["send default or expressions, not both"]


def test_availability_add_branch_and_remove_warns():
    defs, index = _defs()
    plan = plan_property_change({"property": "ELEA/Sifra", "availability": {
        "add": ["ELEA/40.20/*"], "remove": ["ELEA/40.10"]}}, defs, index)
    assert plan.errors == []
    assert plan.payload["availability"] == {
        "add": [{"classificationItemId": {"guid": g}} for g in ["i-40-20", "i-40-20-1"]],
        "remove": [{"classificationItemId": {"guid": "i-40-10"}}]}
    assert plan.changes["availability"] == {"added": ["ELEA/40.20", "ELEA/40.20.1"],
                                            "removed": ["ELEA/40.10"]}
    assert any("not applicable" in w for w in plan.warnings)


def test_availability_set_with_add_is_an_error():
    defs, index = _defs()
    plan = plan_property_change({"property": "ELEA/Sifra", "availability": {
        "set": ["ELEA/40"], "add": ["ELEA/40.10"]}}, defs, index)
    assert plan.errors == ["availability takes either set, or add and/or remove"]


def test_nothing_to_change_is_reported_not_sent():
    defs, index = _defs()
    plan = plan_property_change({"property": "ELEA/Sifra", "name": "Sifra"}, defs, index)
    assert plan.payload == {"propertyId": {"guid": "p-code"}}
    assert plan.warnings == ["nothing to change"]
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_definition_edit.py -v -k "resolve_property or builtin or rename or group or field or default or expressions or availability or nothing"`
Expected: FAIL with `ImportError: cannot import name 'Definitions'`.

- [ ] **Step 3: Implement** (append to `definition_edit.py`; add `from archicad_mcp.core.element_data import value_fit` to the imports):

```python
# ---------- property definitions ----------

_BUILTIN = frozenset({"StaticBuiltIn", "DynamicBuiltIn"})
_ENUM = frozenset({"SingleChoiceEnumeration", "MultipleChoiceEnumeration"})
_SCALAR = {("String", "Default"): "string", ("Integer", "Default"): "integer",
           ("Boolean", "Default"): "boolean", ("Real", "Default"): "number",
           ("Real", "Length"): "length", ("Real", "Area"): "area",
           ("Real", "Volume"): "volume", ("Real", "Angle"): "angle"}
FIELDS = frozenset({"property", "name", "description", "group", "default",
                    "expressions", "availability", "enum"})
LIST_CAP = 20


@dataclass
class PropDef:
    guid: str
    group: str
    name: str
    builtin: bool
    collection: str
    value_type: str
    measure: str
    expressions: list[str] | None
    enum: list[tuple[str, str]]
    description: str
    group_guid: str | None
    default: str | None
    availability: list[str]

    @property
    def address(self) -> str:
        return f"{self.group}/{self.name}"

    @property
    def type_key(self) -> str | None:
        """The Tapir type name (CreatePropertyDefinitions `type`) of this definition."""
        if self.collection == "SingleChoiceEnumeration":
            return "singleEnum"
        if self.collection == "MultipleChoiceEnumeration":
            return "multiEnum"
        scalar = _SCALAR.get((self.value_type, self.measure))
        if scalar and self.collection == "List":
            return scalar + "List"
        return scalar if self.collection == "Single" else None

    def default_state(self):
        return {"expressions": self.expressions} if self.expressions is not None else self.default


class Definitions:
    """Every property definition, by address ("Group/Name", custom only) and guid."""

    def __init__(self, props: list[PropDef], groups: dict[str, str]):
        self.by_guid = {p.guid: p for p in props}
        self.by_address = {p.address: p for p in props if not p.builtin}
        self.groups = groups

    @classmethod
    def load(cls, conn: ArchicadConnection) -> "Definitions":
        raw = conn.tapir("GetAllProperties")
        props = []
        for p in raw.get("properties", []):
            props.append(PropDef(
                guid=p["propertyId"]["guid"], group=p.get("propertyGroupName", ""),
                name=p.get("propertyName", ""), builtin=p.get("propertyType") in _BUILTIN,
                collection=p.get("propertyCollectionType", ""),
                value_type=p.get("propertyValueType", ""),
                measure=p.get("propertyMeasureType", ""),
                expressions=p.get("expressions") if p.get("isExpressionBased") else None,
                enum=[(e["enumValue"].get("guid", ""), e["enumValue"].get("displayValue", ""))
                      for e in p.get("possibleEnumValues", [])],
                description=p.get("propertyDescription", ""),
                group_guid=p.get("propertyGroupId", {}).get("guid"),
                default=p.get("defaultValueDisplay"),
                availability=[a["classificationItemId"]["guid"]
                              for a in p.get("availability", [])]))
        groups = {g["name"]: g["propertyGroupId"]["guid"]
                  for g in raw.get("propertyGroups", []) if g.get("isCustom")}
        return cls(props, groups)

    def resolve(self, ref: str) -> tuple[PropDef | None, str | None]:
        # Whole-string match: group names may contain "/", so never split.
        p = self.by_address.get(ref) or self.by_guid.get(ref)
        if p is None:
            return None, (f"no property '{ref}'; address custom properties as Group/Name "
                          "(search_definitions finds the exact address) or by GUID")
        return p, None


@dataclass
class PlannedEdit:
    target: str
    payload: dict
    changes: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def entry(self) -> dict:
        out = {"target": self.target, "changes": self.changes}
        if self.warnings:
            out["warnings"] = self.warnings
        if self.errors:
            out["errors"] = self.errors
        return out


def _capped(labels: list[str]) -> list[str]:
    labels = sorted(labels)
    if len(labels) > LIST_CAP:
        return labels[:LIST_CAP] + [f"... and {len(labels) - LIST_CAP} more"]
    return labels


def _default_payload(p: PropDef, value, options: list[str]) -> tuple[dict | None, str | None]:
    if value is None:
        return {"basicDefaultValue": {"status": "userUndefined"}}, None
    key = p.type_key
    if key == "singleEnum":
        if options.count(value) != 1:
            return None, f"default '{value}' is not exactly one option of {options}"
        return {"basicDefaultValue": {"status": "normal", "type": key, "value": {
            "type": "displayValue", "displayValue": value}}}, None
    if key == "multiEnum":
        values = value if isinstance(value, list) else [value]
        bad = [v for v in values if options.count(v) != 1]
        if bad:
            return None, f"default options {bad} are not exactly one option each of {options}"
        return {"basicDefaultValue": {"status": "normal", "type": key, "value": [
            {"enumValueId": {"type": "displayValue", "displayValue": v}} for v in values]}}, None
    if key is None:
        return None, f"cannot set a default on a {p.collection} {p.value_type} property"
    if key.endswith("List"):
        if not isinstance(value, list):
            return None, f"'{key}' default takes a list"
        sent = []
        for v in value:
            fitted, err = value_fit(key[:-4], v)
            if err:
                return None, err
            sent.append(fitted)
        return {"basicDefaultValue": {"status": "normal", "type": key, "value": sent}}, None
    fitted, err = value_fit(key, value)
    if err:
        return None, err
    return {"basicDefaultValue": {"status": "normal", "type": key, "value": fitted}}, None


def _plan_availability(spec: dict, p: PropDef, index: ClassificationIndex,
                       plan: PlannedEdit) -> None:
    if "set" in spec and ("add" in spec or "remove" in spec):
        plan.errors.append("availability takes either set, or add and/or remove")
        return

    def expand(addresses: list[str]) -> list[str]:
        out: list[str] = []
        for a in addresses:
            guids, err = index.resolve(a)
            if err:
                plan.errors.append(err)
            out.extend(g for g in guids if g not in out)
        return out

    def ids(guids: list[str]) -> list[dict]:
        return [{"classificationItemId": {"guid": g}} for g in guids]

    current = set(p.availability)
    if "set" in spec:
        wanted = expand(spec["set"])
        new = set(wanted)
        plan.payload["availability"] = {"set": ids(wanted)}
    else:
        add, remove = expand(spec.get("add", [])), expand(spec.get("remove", []))
        new = (current | set(add)) - set(remove)
        payload = {}
        if add:
            payload["add"] = ids(add)
        if remove:
            payload["remove"] = ids(remove)
        plan.payload["availability"] = payload
    added, removed = new - current, current - new
    if added or removed:
        plan.changes["availability"] = {"added": _capped([index.label(g) for g in added]),
                                        "removed": _capped([index.label(g) for g in removed])}
    if removed:
        plan.warnings.append(
            f"no longer available for {len(removed)} classification item(s): values on "
            "elements with those classifications become not applicable")


def plan_property_change(change: dict, defs: Definitions,
                         index: ClassificationIndex | None) -> PlannedEdit:
    ref = str(change.get("property", ""))
    p, err = defs.resolve(ref)
    if p is None:
        return PlannedEdit(ref, {}, errors=[err])
    plan = PlannedEdit(p.address if not p.builtin else ref, {"propertyId": {"guid": p.guid}})
    if p.builtin:
        plan.errors.append("built-in properties cannot be edited")
        return plan
    unknown = sorted(set(change) - FIELDS)
    if unknown:
        plan.errors.append(f"unknown field(s) {unknown}; allowed: {sorted(FIELDS - {'property'})}")
        return plan

    new_name, new_group = p.name, p.group
    if "name" in change and change["name"] != p.name:
        new_name = change["name"]
        plan.payload["name"] = new_name
        plan.changes["name"] = [p.name, new_name]
    if "description" in change and change["description"] != p.description:
        plan.payload["description"] = change["description"]
        plan.changes["description"] = [p.description, change["description"]]
    if "group" in change and change["group"] != p.group:
        guid = defs.groups.get(change["group"])
        if guid is None:
            plan.errors.append(f"no custom property group '{change['group']}'; create it "
                               "first (Tapir CreatePropertyGroups)")
        else:
            new_group = change["group"]
            plan.payload["groupId"] = {"guid": guid}
            plan.changes["group"] = [p.group, new_group]
    target = f"{new_group}/{new_name}"
    if target != p.address and target in defs.by_address:
        plan.errors.append(f"'{target}' already exists")

    options = [d for _, d in p.enum]
    if "enum" in change:
        options = _plan_enum(change["enum"], p, plan)

    if "default" in change and "expressions" in change:
        plan.errors.append("send default or expressions, not both")
    elif "expressions" in change:
        exprs = change["expressions"]
        if not isinstance(exprs, list) or not exprs:
            plan.errors.append("expressions takes a non-empty list of expression strings")
        else:
            plan.payload["defaultValue"] = {"expressions": exprs}
            plan.changes["default"] = [p.default_state(), {"expressions": exprs}]
    elif "default" in change:
        payload, err = _default_payload(p, change["default"], options)
        if err:
            plan.errors.append(err)
        else:
            plan.payload["defaultValue"] = payload
            plan.changes["default"] = [p.default_state(), change["default"]]

    if "availability" in change:
        if index is None:
            plan.errors.append("availability needs the classification index")
        else:
            _plan_availability(change["availability"], p, index, plan)

    if len(plan.payload) == 1 and not plan.errors:
        plan.warnings.append("nothing to change")
    return plan


def _plan_enum(spec: dict, p: PropDef, plan: PlannedEdit) -> list[str]:
    """Filled in by Task 10. Returns the option texts after the edit."""
    return [d for _, d in p.enum]
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_definition_edit.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/core/definition_edit.py tests/test_definition_edit.py
git commit -m "feat: plan property definition edits (name, group, default, availability)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Enum option edits in the plan

**Files:**
- Modify: `src/archicad_mcp/core/definition_edit.py` (`_plan_enum`)
- Test: `tests/test_definition_edit.py`

**Interfaces:**
- Consumes: `PropDef`, `PlannedEdit` (Task 9).
- Produces: `_plan_enum(spec: dict, p: PropDef, plan: PlannedEdit) -> list[str]`; payload keys `renameEnumValues`, `removeEnumValues`, `possibleEnumValues`, `enumOrder` exactly as Task 2 defines them.

- [ ] **Step 1: Failing tests**

```python
def test_enum_rename_remove_add_order():
    defs, index = _defs()
    plan = plan_property_change({"property": "ELEA/Kategorija", "enum": {
        "rename": {"Kuhinja": "Kuhinjska oprema"}, "remove": ["Staro"],
        "add": ["Pisarna"], "order": ["Pisarna", "Kuhinjska oprema", "Sanitarije"]},
        "default": "Kuhinjska oprema"}, defs, index)
    assert plan.errors == []
    assert plan.payload["renameEnumValues"] == [
        {"enumValueId": {"guid": "e-k"}, "displayValue": "Kuhinjska oprema"}]
    assert plan.payload["removeEnumValues"] == [{"enumValueId": {"guid": "e-o"}}]
    assert plan.payload["possibleEnumValues"] == [{"enumValue": {"displayValue": "Pisarna"}}]
    assert plan.payload["enumOrder"] == ["Pisarna", "Kuhinjska oprema", "Sanitarije"]
    assert plan.changes["enum"] == [["Kuhinja", "Sanitarije", "Staro"],
                                    ["Pisarna", "Kuhinjska oprema", "Sanitarije"]]
    assert any("Staro" in w and "lose" in w for w in plan.warnings)


def test_enum_text_matching_two_options_is_an_error():
    defs, index = _defs()
    plan = plan_property_change({"property": "ELEA/Dvojnik",
                                 "enum": {"remove": ["A"]}}, defs, index)
    assert plan.errors == ["enum option 'A' matches 2 options; address it by its GUID"]


def test_unknown_enum_option_is_an_error():
    defs, index = _defs()
    plan = plan_property_change({"property": "ELEA/Kategorija",
                                 "enum": {"rename": {"Nope": "X"}}}, defs, index)
    assert plan.errors == ["enum option 'Nope' does not exist; options: "
                           "['Kuhinja', 'Sanitarije', 'Staro']"]


def test_removing_the_default_option_needs_a_new_default():
    defs, index = _defs()
    plan = plan_property_change({"property": "ELEA/Kategorija",
                                 "enum": {"remove": ["Kuhinja"]}}, defs, index)
    assert plan.errors == ["'Kuhinja' is the default; send a new default in the same change"]


def test_order_must_name_every_option_once():
    defs, index = _defs()
    plan = plan_property_change({"property": "ELEA/Kategorija",
                                 "enum": {"order": ["Staro", "Kuhinja"]}}, defs, index)
    assert plan.errors[0].startswith("order must list every option exactly once")


def test_enum_edit_on_a_non_enum_is_an_error():
    defs, index = _defs()
    plan = plan_property_change({"property": "ELEA/Sifra",
                                 "enum": {"add": ["X"]}}, defs, index)
    assert plan.errors == ["'ELEA/Sifra' is not an enumeration property"]


def test_rename_and_remove_of_the_same_option_is_an_error():
    defs, index = _defs()
    plan = plan_property_change({"property": "ELEA/Kategorija", "enum": {
        "rename": {"Staro": "Old"}, "remove": ["Staro"]}}, defs, index)
    assert plan.errors == ["enum option 'Staro' is both renamed and removed"]


def test_adding_an_existing_option_is_a_no_op():
    defs, index = _defs()
    plan = plan_property_change({"property": "ELEA/Kategorija",
                                 "enum": {"add": ["Staro"]}}, defs, index)
    assert "possibleEnumValues" not in plan.payload
    assert plan.warnings == ["nothing to change"]
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_definition_edit.py -v -k "enum or order or option"`
Expected: FAIL (stub returns options unchanged, no payload).

- [ ] **Step 3: Replace the `_plan_enum` stub.** Options are tracked as (guid, text) pairs, so a rename or removal hits exactly the option named even when two options share a text. An option may be named by its text or by its GUID (the GUID is how the duplicate-text case is resolved).

```python
_ENUM_KEYS = frozenset({"rename", "remove", "add", "order"})


def _plan_enum(spec: dict, p: PropDef, plan: PlannedEdit) -> list[str]:
    """Plan enum option edits, applied in Tapir's order: rename, remove, add,
    order. Returns the option texts after the edit."""
    current = [d for _, d in p.enum]
    if p.collection not in _ENUM:
        plan.errors.append(f"'{p.address}' is not an enumeration property")
        return current
    unknown = sorted(set(spec) - _ENUM_KEYS)
    if unknown:
        plan.errors.append(f"unknown enum field(s) {unknown}; allowed: {sorted(_ENUM_KEYS)}")
        return current

    def guid_of(ref: str) -> str | None:
        if any(g == ref for g, _ in p.enum):
            return ref
        guids = [g for g, d in p.enum if d == ref]
        if len(guids) == 1:
            return guids[0]
        plan.errors.append(
            f"enum option '{ref}' does not exist; options: {current}" if not guids else
            f"enum option '{ref}' matches {len(guids)} options; address it by its GUID")
        return None

    renames: dict[str, str] = spec.get("rename", {})
    removes: list[str] = spec.get("remove", [])
    for ref in sorted(set(renames) & set(removes)):
        plan.errors.append(f"enum option '{ref}' is both renamed and removed")
    if plan.errors:
        return current

    after: list[list[str]] = [[g, d] for g, d in p.enum]
    if renames:
        out = []
        for ref, new in renames.items():
            guid = guid_of(ref)
            if guid:
                out.append({"enumValueId": {"guid": guid}, "displayValue": new})
                next(o for o in after if o[0] == guid)[1] = new
        plan.payload["renameEnumValues"] = out
    if removes:
        out, gone = [], []
        for ref in removes:
            guid = guid_of(ref)
            if guid:
                out.append({"enumValueId": {"guid": guid}})
                gone.append(guid)
        after = [o for o in after if o[0] not in gone]
        plan.payload["removeEnumValues"] = out
        plan.warnings.append(
            f"removing {removes}: elements holding these options lose that value. Not "
            "counted, because counting needs property value reads, which can crash Archicad")
    texts = [d for _, d in after]
    adds = [t for t in spec.get("add", []) if t not in texts]
    if adds:
        plan.payload["possibleEnumValues"] = [{"enumValue": {"displayValue": t}} for t in adds]
        texts.extend(adds)
    if "order" in spec:
        order = list(spec["order"])
        if sorted(order) != sorted(texts) or len(set(order)) != len(order):
            plan.errors.append(f"order must list every option exactly once: {texts}")
        else:
            plan.payload["enumOrder"] = order
            texts = order
    if texts != current:
        plan.changes["enum"] = [current, texts]
    return texts
```

The enum plan cannot know whether a new default follows in the same change, so the "removed option is the default" check lives in the caller. In `plan_property_change`, directly after the `default` / `expressions` block and before the availability block, add:

```python
    removed = {g for g in (r["enumValueId"]["guid"]
                           for r in plan.payload.get("removeEnumValues", []))}
    removed_texts = {d for g, d in p.enum if g in removed}
    if p.default in removed_texts and "defaultValue" not in plan.payload:
        plan.errors.append(f"'{p.default}' is the default; send a new default in the same change")
```

`enumOrder` goes to Tapir as option texts, so it needs distinct texts; with duplicates Tapir refuses the item (Task 2 Step 4), and the plan above refuses it first because `sorted(order) != sorted(texts)` cannot be satisfied by a list of unique entries.

- [ ] **Step 4: Run all definition tests**

Run: `uv run pytest tests/test_definition_edit.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/core/definition_edit.py tests/test_definition_edit.py
git commit -m "feat: plan enum option rename, remove, add and reorder by option text

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: `edit_property_definitions` entry point (dry run, commit, re-read)

**Files:**
- Modify: `src/archicad_mcp/core/definition_edit.py`
- Test: `tests/test_definition_edit.py`

**Interfaces:**
- Consumes: everything from Tasks 8-10; `error_fields` from `archicad_mcp.core.element_data`.
- Produces: `edit_property_definitions(conn, changes: list[dict], dry_run: bool = True) -> dict` with keys `dry_run`, `planned` (entries), `skipped` (entries with errors), and after a commit `applied` ([{target, now}]), `failed` (grouped), or `error`.

- [ ] **Step 1: Failing tests**

```python
from archicad_mcp.core.definition_edit import edit_property_definitions


def test_dry_run_sends_nothing():
    conn, core = _conn()
    result = edit_property_definitions(conn, [{"property": "ELEA/Sifra", "name": "X"}])
    assert result["dry_run"] is True
    assert result["planned"][0]["changes"] == {"name": ["Sifra", "X"]}
    assert not any(cmd == "UpdatePropertyDefinitions" for cmd, _ in core.calls)


def test_commit_sends_one_batch_of_valid_changes_and_skips_bad_ones():
    conn, core = _conn()
    result = edit_property_definitions(conn, [
        {"property": "ELEA/Sifra", "name": "X"},
        {"property": "ELEA/Nope", "name": "Y"},
        {"property": "ELEA/Dolzina", "description": "d"}], dry_run=False)
    sent = [p for cmd, p in core.calls if cmd == "UpdatePropertyDefinitions"]
    assert len(sent) == 1
    assert [i["propertyId"]["guid"] for i in sent[0]["propertyDefinitions"]] == ["p-code", "p-len"]
    assert result["skipped"][0]["target"] == "ELEA/Nope"
    assert [a["target"] for a in result["applied"]] == ["ELEA/Sifra", "ELEA/Dolzina"]


def test_nothing_to_change_is_not_sent():
    conn, core = _conn()
    edit_property_definitions(conn, [{"property": "ELEA/Sifra", "name": "Sifra"}], dry_run=False)
    assert not any(cmd == "UpdatePropertyDefinitions" for cmd, _ in core.calls)


def test_archicad_refusals_are_grouped():
    def refuse(params):
        return {"executionResults": [{"success": False, "error": {
            "code": 1, "message": "no access right: in Teamwork this needs the right "
                                  "to modify properties or classifications"}}
            for _ in params["propertyDefinitions"]]}
    conn, _ = _conn(update=refuse)
    result = edit_property_definitions(conn, [{"property": "ELEA/Sifra", "name": "X"},
                                              {"property": "ELEA/Dolzina", "name": "Y"}],
                                       dry_run=False)
    assert result["failed"][0]["count"] == 2
    assert "Teamwork" in result["failed"][0]["message"]


def test_no_property_values_are_ever_read():
    conn, core = _conn()
    edit_property_definitions(conn, [{"property": "ELEA/Kategorija",
                                      "enum": {"add": ["X"]}}], dry_run=False)
    assert not any("PropertyValues" in cmd for cmd, _ in core.calls)
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_definition_edit.py -v -k "dry_run or commit or refusals or values or not_sent"`
Expected: FAIL with `ImportError: cannot import name 'edit_property_definitions'`.

- [ ] **Step 3: Implement** (add `from multiconn_archicad.errors import APIErrorBase` and `from archicad_mcp.connection import ArchicadUnavailableError` and `error_fields` to imports):

```python
def _now(p: PropDef, index: ClassificationIndex | None, keys) -> dict:
    state = {"name": p.name, "group": p.group, "description": p.description,
             "default": p.default_state(), "enum": [d for _, d in p.enum]}
    if index is not None:
        state["availability"] = _capped([index.label(g) for g in p.availability])
    return {k: state[k] for k in keys if k in state}


def edit_property_definitions(conn: ArchicadConnection, changes: list[dict],
                              dry_run: bool = True) -> dict:
    gate = editing_unavailable(conn)
    if gate:
        return gate
    defs = Definitions.load(conn)
    index = (ClassificationIndex.load(conn)
             if any("availability" in c for c in changes) else None)
    plans = [plan_property_change(c, defs, index) for c in changes]
    to_send = [p for p in plans if not p.errors and len(p.payload) > 1]
    result: dict = {"dry_run": dry_run,
                    "planned": [p.entry() for p in plans if not p.errors]}
    skipped = [p.entry() for p in plans if p.errors]
    if skipped:
        result["skipped"] = skipped
    if dry_run or not to_send:
        return result

    try:
        response = conn.tapir("UpdatePropertyDefinitions",
                              {"propertyDefinitions": [p.payload for p in to_send]})
    except (APIErrorBase, ArchicadUnavailableError) as exc:
        result["error"] = error_fields(exc)
        return result
    applied, failed = split_results([p.target for p in to_send], response)
    after = Definitions.load(conn)
    by_target = {p.target: p for p in to_send}
    result["applied"] = [
        {"target": t, "now": _now(after.by_guid[by_target[t].payload["propertyId"]["guid"]],
                                  index, by_target[t].changes)}
        for t in applied if by_target[t].payload["propertyId"]["guid"] in after.by_guid]
    if failed:
        result["failed"] = group_by_error(failed)
    return result
```

- [ ] **Step 4: Run all definition tests**

Run: `uv run pytest tests/test_definition_edit.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/core/definition_edit.py tests/test_definition_edit.py
git commit -m "feat: edit_property_definitions dry run, batch commit and re-read

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: `edit_classifications` and `import_definitions`

**Files:**
- Create: `src/archicad_mcp/core/classification_edit.py` (split out of `definition_edit.py` to keep that file focused; the spec's plumbing named one file, this is the same code in two)
- Create: `src/archicad_mcp/core/definition_xml.py`
- Create: `tests/fixtures/xml/properties_mcp_test.xml`, `tests/fixtures/xml/classifications_mcp_test.xml` (Task 1 exports, trimmed to "MCP Test")
- Test: `tests/test_classification_edit.py`, `tests/test_definition_xml.py`

**Interfaces:**
- Consumes: `ClassificationIndex`, `Definitions`, `editing_unavailable`, `split_results`, `group_by_error`, `PlannedEdit` (Tasks 8-11).
- Produces: `edit_classifications(conn, changes: list[dict], dry_run: bool = True) -> dict`; `parse_property_xml(text: str) -> list[tuple[str, str]]` ((group, name)); `parse_classification_xml(text: str) -> list[tuple[str, list[str]]]` ((system, codes)); `import_definitions(conn, kind: str, xml_path: str, conflict: str, item_conflict: str = "skip", dry_run: bool = True) -> dict`.

- [ ] **Step 1: Failing classification tests** `tests/test_classification_edit.py`:

```python
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.classification_edit import edit_classifications
from tests.fixtures.definition_edit_replays import fake_editing_core


def _conn(**kw):
    core = fake_editing_core(**kw)
    return ArchicadConnection(19724, core=core), core


def test_item_code_and_name_change_plan():
    conn, core = _conn()
    result = edit_classifications(conn, [{"item": "ELEA/40.10", "code": "40.11",
                                          "name": "Kuhinjska oprema"}])
    assert result["planned"][0] == {"target": "ELEA/40.10", "changes": {
        "code": ["40.10", "40.11"], "name": ["Kuhinja", "Kuhinjska oprema"]}}
    assert not any(cmd.startswith("Update") for cmd, _ in core.calls)


def test_code_colliding_with_a_sibling_is_an_error():
    conn, _ = _conn()
    result = edit_classifications(conn, [{"item": "ELEA/40.10", "code": "40.20"}])
    assert result["skipped"][0]["errors"] == ["code '40.20' is already used by a sibling (ELEA/40.20)"]


def test_branch_address_is_refused_for_item_edits():
    conn, _ = _conn()
    result = edit_classifications(conn, [{"item": "ELEA/40/*", "name": "X"}])
    assert "one item" in result["skipped"][0]["errors"][0]


def test_system_rename_and_bad_date():
    conn, _ = _conn()
    result = edit_classifications(conn, [
        {"system": "ELEA", "name": "ELEA 2026", "version": "2"},
        {"system": "ELEA 2", "date": "25.9.2026"}])
    assert result["planned"][0]["changes"] == {"name": ["ELEA", "ELEA 2026"], "version": ["1", "2"]}
    assert result["skipped"][0]["errors"] == ["date must be YYYY-MM-DD"]


def test_system_rename_onto_existing_name_is_an_error():
    conn, _ = _conn()
    result = edit_classifications(conn, [{"system": "ELEA", "name": "ELEA 2"}])
    assert result["skipped"][0]["errors"] == ["a classification system 'ELEA 2' already exists"]


def test_commit_sends_systems_then_items():
    conn, core = _conn()
    edit_classifications(conn, [{"item": "ELEA/40.10", "name": "K"},
                                {"system": "ELEA", "description": "d"}], dry_run=False)
    order = [cmd for cmd, _ in core.calls if cmd.startswith("Update")]
    assert order == ["UpdateClassificationSystems", "UpdateClassificationItems"]
    items = [p for cmd, p in core.calls if cmd == "UpdateClassificationItems"][0]
    assert items == {"classificationItems": [
        {"classificationItemId": {"guid": "i-40-10"}, "name": "K"}]}
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_classification_edit.py -v`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement** `src/archicad_mcp/core/classification_edit.py`:

```python
"""edit_classifications: in-place edits of classification systems and items.

Items keep their GUID through a code or name change, so elements classified with
them and properties available for them stay attached. Reparenting is not
offered: the API item has no parent field, and delete plus recreate would
detach every element.
"""
from __future__ import annotations

import re

from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.connection import ArchicadConnection, ArchicadUnavailableError
from archicad_mcp.core.definition_edit import (
    ClassificationIndex, PlannedEdit, editing_unavailable, group_by_error, split_results)
from archicad_mcp.core.element_data import error_fields

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SYSTEM_FIELDS = {"name": "name", "description": "description", "source": "source",
                  "version": "version", "date": "date"}
_ITEM_FIELDS = {"code": "id", "name": "name", "description": "description"}


def _plan_system(change: dict, index: ClassificationIndex) -> PlannedEdit:
    ref = str(change["system"])
    system = index.systems.get(ref) or index.system_of_guid(ref)
    if system is None:
        return PlannedEdit(ref, {}, errors=[f"no classification system '{ref}'; "
                                            f"systems here: {sorted(index.systems)}"])
    plan = PlannedEdit(system.name, {"classificationSystemId": {"guid": system.guid}})
    unknown = sorted(set(change) - {"system", *_SYSTEM_FIELDS})
    if unknown:
        plan.errors.append(f"unknown field(s) {unknown}; allowed: {sorted(_SYSTEM_FIELDS)}")
        return plan
    for key, wire in _SYSTEM_FIELDS.items():
        if key in change and change[key] != getattr(system, key):
            plan.payload[wire] = change[key]
            plan.changes[key] = [getattr(system, key), change[key]]
    if "date" in change and not _DATE.match(str(change["date"])):
        plan.errors.append("date must be YYYY-MM-DD")
    new_name = change.get("name", system.name)
    if new_name != system.name and new_name in index.systems:
        plan.errors.append(f"a classification system '{new_name}' already exists")
    if len(plan.payload) == 1 and not plan.errors:
        plan.warnings.append("nothing to change")
    return plan


def _plan_item(change: dict, index: ClassificationIndex) -> PlannedEdit:
    ref = str(change["item"])
    if ref.endswith("/*"):
        return PlannedEdit(ref, {}, errors=["an item edit names one item; drop the '/*'"])
    guids, err = index.resolve(ref)
    if err:
        return PlannedEdit(ref, {}, errors=[err])
    item = index.items[guids[0]]
    plan = PlannedEdit(index.label(item.guid), {"classificationItemId": {"guid": item.guid}})
    unknown = sorted(set(change) - {"item", *_ITEM_FIELDS})
    if unknown:
        plan.errors.append(f"unknown field(s) {unknown}; allowed: {sorted(_ITEM_FIELDS)}")
        return plan
    for key, wire in _ITEM_FIELDS.items():
        current = item.code if key == "code" else getattr(item, key)
        if key in change and change[key] != current:
            plan.payload[wire] = change[key]
            plan.changes[key] = [current, change[key]]
    if "code" in plan.changes:
        clash = [g for g in index.siblings(item.guid) if index.items[g].code == change["code"]]
        if clash:
            plan.errors.append(f"code '{change['code']}' is already used by a sibling "
                               f"({index.label(clash[0])})")
    if len(plan.payload) == 1 and not plan.errors:
        plan.warnings.append("nothing to change")
    return plan


def edit_classifications(conn: ArchicadConnection, changes: list[dict],
                         dry_run: bool = True) -> dict:
    gate = editing_unavailable(conn)
    if gate:
        return gate
    index = ClassificationIndex.load(conn)
    plans: list[tuple[str, PlannedEdit]] = []
    for change in changes:
        if ("system" in change) == ("item" in change):
            plans.append(("?", PlannedEdit(str(change), {}, errors=[
                "give exactly one of 'system' or 'item'"])))
        elif "system" in change:
            plans.append(("system", _plan_system(change, index)))
        else:
            plans.append(("item", _plan_item(change, index)))

    result: dict = {"dry_run": dry_run,
                    "planned": [p.entry() for _, p in plans if not p.errors]}
    skipped = [p.entry() for _, p in plans if p.errors]
    if skipped:
        result["skipped"] = skipped
    batches = [
        ("UpdateClassificationSystems", "classificationSystems",
         [p for k, p in plans if k == "system" and not p.errors and len(p.payload) > 1]),
        ("UpdateClassificationItems", "classificationItems",
         [p for k, p in plans if k == "item" and not p.errors and len(p.payload) > 1]),
    ]
    if dry_run or not any(b for _, _, b in batches):
        return result

    applied, failed = [], []
    for command, key, batch in batches:
        if not batch:
            continue
        try:
            response = conn.tapir(command, {key: [p.payload for p in batch]})
        except (APIErrorBase, ArchicadUnavailableError) as exc:
            result["error"] = error_fields(exc)
            break
        ok, bad = split_results([p.target for p in batch], response)
        applied += ok
        failed += bad
    result["applied"] = applied
    if failed:
        result["failed"] = group_by_error(failed)
    return result
```

- [ ] **Step 4: Run** `uv run pytest tests/test_classification_edit.py -v`. Expected: all PASS.

- [ ] **Step 5: XML fixtures.** Copy the Task 1 exports into `tests/fixtures/xml/`, then delete every property group other than "MCP Test" and every system other than "MCP Test" from the copies. Grep them for any office name before committing:

Run: `grep -ciE "elea|cvp|oprema" tests/fixtures/xml/*.xml`
Expected: `0` for both files.

- [ ] **Step 6: Failing XML tests** `tests/test_definition_xml.py`:

```python
from pathlib import Path

from archicad_mcp.core.definition_xml import parse_classification_xml, parse_property_xml

XML = Path(__file__).parent / "fixtures" / "xml"


def test_real_property_export_lists_group_and_name():
    pairs = parse_property_xml((XML / "properties_mcp_test.xml").read_text(encoding="utf-8"))
    assert ("MCP Test", "Fire Rating") in pairs


def test_real_classification_export_lists_system_and_codes():
    systems = parse_classification_xml(
        (XML / "classifications_mcp_test.xml").read_text(encoding="utf-8"))
    names = [s for s, _ in systems]
    assert names == ["MCP Test"]
    assert {"Building", "Wall", "Slab", "Object", "Site"} <= set(systems[0][1])


def test_malformed_xml_raises_value_error():
    import pytest
    with pytest.raises(ValueError, match="not valid XML"):
        parse_property_xml("<unclosed>")
```

(Adjust the expected codes to what the trimmed export actually contains; the memory note says the "MCP Test" system has Building > Wall/Slab/Object, and Site.)

- [ ] **Step 7: Implement** `src/archicad_mcp/core/definition_xml.py`. The tag constants come from Task 1 Step 4's note; the values below are the expected Archicad dialect and must be replaced by the recorded ones if they differ:

```python
"""Just enough of the Property / Classification Manager XML to preview an import:
which names it carries. Archicad does the real parsing on import."""
from __future__ import annotations

import xml.etree.ElementTree as ET

# From the Oprema-objekti exports (Task 1). Matched by local name, so a namespace
# prefix does not matter.
PROPERTY_GROUP = "PropertyDefinitionGroup"
PROPERTY = "PropertyDefinition"
SYSTEM = "System"
ITEM = "Item"
NAME = "Name"
CODE = "ID"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _root(text: str) -> ET.Element:
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"not valid XML: {exc}") from exc


def _child_text(el: ET.Element, name: str) -> str:
    for child in el:
        if _local(child.tag) == name:
            return (child.text or "").strip()
    return ""


def parse_property_xml(text: str) -> list[tuple[str, str]]:
    out = []
    for group in _root(text).iter():
        if _local(group.tag) != PROPERTY_GROUP:
            continue
        group_name = _child_text(group, NAME)
        for prop in group.iter():
            if _local(prop.tag) == PROPERTY:
                out.append((group_name, _child_text(prop, NAME)))
    return out


def parse_classification_xml(text: str) -> list[tuple[str, list[str]]]:
    out = []
    for system in _root(text).iter():
        if _local(system.tag) != SYSTEM:
            continue
        codes = [_child_text(item, CODE) for item in system.iter() if _local(item.tag) == ITEM]
        out.append((_child_text(system, NAME), codes))
    return out
```

- [ ] **Step 8: Run** `uv run pytest tests/test_definition_xml.py -v`. Expected: all PASS. If a real-file test fails, fix the constants from the actual file, not the test.

- [ ] **Step 9: `import_definitions`.** Failing tests appended to `tests/test_classification_edit.py`:

```python
from pathlib import Path

from archicad_mcp.core.classification_edit import import_definitions

XML = Path(__file__).parent / "fixtures" / "xml"


def test_import_dry_run_reports_new_and_colliding(tmp_path):
    conn, core = _conn()
    xml = (XML / "properties_mcp_test.xml").read_text(encoding="utf-8")
    result = import_definitions(conn, "property", str(XML / "properties_mcp_test.xml"), "skip")
    assert result["dry_run"] is True
    assert "new" in result and "collisions" in result and "policy" in result
    assert not any(cmd.startswith("Import") for cmd, _ in core.calls)


def test_import_rejects_an_unknown_policy():
    conn, _ = _conn()
    result = import_definitions(conn, "property", str(XML / "properties_mcp_test.xml"), "merge")
    assert result["error"] == "conflict for property imports is one of ['append', 'replace', 'skip']"


def test_import_commit_sends_the_file_and_reports_created(tmp_path):
    created = {"executionResult": {"success": True},
               "created": [{"guid": "p-code"}], "removed": []}
    conn, core = _conn(extra_tapir={"ImportPropertiesXml": created})
    result = import_definitions(conn, "property", str(XML / "properties_mcp_test.xml"),
                                "append", dry_run=False)
    sent = [p for cmd, p in core.calls if cmd == "ImportPropertiesXml"][0]
    assert sent["conflictPolicy"] == "append" and sent["xml"].lstrip().startswith("<")
    assert result["created"] == ["ELEA/Sifra"]


def test_import_refuses_a_missing_file():
    conn, _ = _conn()
    result = import_definitions(conn, "property", "/nope/missing.xml", "skip")
    assert "cannot read" in result["error"]
```

Implementation, appended to `classification_edit.py` (add `from pathlib import Path` and `from archicad_mcp.core.definition_edit import Definitions`, `from archicad_mcp.core.definition_xml import parse_classification_xml, parse_property_xml`):

```python
POLICIES = {"property": ["append", "replace", "skip"],
            "classification": ["merge", "replace", "skip"]}
ITEM_POLICIES = ["replace", "skip"]
MAX_XML_BYTES = 20 * 1024 * 1024
POLICY_EFFECT = {
    "append": "colliding definitions are imported under a new, unused name; existing ones stay",
    "replace": "colliding definitions are replaced by the imported ones (see docs/known-issues.md "
               "for whether their GUID, and so element values, survive)",
    "skip": "colliding definitions stay as they are; the imported ones are dropped",
    "merge": "colliding systems are merged: items are added, and colliding items follow item_conflict",
}


def import_definitions(conn: ArchicadConnection, kind: str, xml_path: str, conflict: str,
                       item_conflict: str = "skip", dry_run: bool = True) -> dict:
    if kind not in POLICIES:
        return {"error": "kind is 'property' or 'classification'"}
    if conflict not in POLICIES[kind]:
        return {"error": f"conflict for {kind} imports is one of {POLICIES[kind]}"}
    if kind == "classification" and item_conflict not in ITEM_POLICIES:
        return {"error": f"item_conflict is one of {ITEM_POLICIES}"}
    path = Path(xml_path).expanduser()
    try:
        if path.stat().st_size > MAX_XML_BYTES:
            return {"error": f"{path} is larger than 20 MB"}
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"error": f"cannot read {path}: {exc.strerror or exc}"}
    gate = editing_unavailable(conn)
    if gate:
        return gate

    try:
        if kind == "property":
            defs = Definitions.load(conn)
            names = [f"{g}/{n}" for g, n in parse_property_xml(text)]
            existing = set(defs.by_address)
        else:
            index = ClassificationIndex.load(conn)
            names = [f"{s}/{c}" for s, codes in parse_classification_xml(text) for c in codes]
            existing = {index.label(g) for g in index.items}
    except ValueError as exc:
        return {"error": str(exc)}
    collisions = [n for n in names if n in existing]
    result: dict = {"dry_run": dry_run, "kind": kind,
                    "new": _capped_names([n for n in names if n not in existing]),
                    "collisions": _capped_names(collisions),
                    "policy": POLICY_EFFECT[conflict]}
    if dry_run:
        return result

    if kind == "property":
        command, params = "ImportPropertiesXml", {"xml": text, "conflictPolicy": conflict}
    else:
        command, params = "ImportClassificationsXml", {
            "xml": text, "systemConflictPolicy": conflict, "itemConflictPolicy": item_conflict}
    try:
        response = conn.tapir(command, params)
    except (APIErrorBase, ArchicadUnavailableError) as exc:
        result["error"] = error_fields(exc)
        return result
    outcome = response.get("executionResult", {})
    if not outcome.get("success"):
        result["error"] = outcome.get("error", {}).get("message", "import refused")
        return result
    created = [c["guid"] for c in response.get("created", [])]
    removed = [c["guid"] for c in response.get("removed", [])]
    if kind == "property":
        after = Definitions.load(conn)
        label = lambda g: after.by_guid[g].address if g in after.by_guid else g
    else:
        after_index = ClassificationIndex.load(conn)
        label = after_index.label
    result["created"] = _capped_names([label(g) for g in created])
    result["removed"] = _capped_names(removed)
    return result


def _capped_names(names: list[str], cap: int = 50) -> list[str]:
    return names if len(names) <= cap else names[:cap] + [f"... and {len(names) - cap} more"]
```

The `created` label for a classification system guid falls back to the guid (the index labels items only); acceptable, since the system name also appears in `new`.

- [ ] **Step 10: Run** `uv run pytest tests/test_classification_edit.py tests/test_definition_xml.py -v`. Expected: all PASS.

- [ ] **Step 11: Commit**

```bash
git add src/archicad_mcp/core/classification_edit.py src/archicad_mcp/core/definition_xml.py tests/test_classification_edit.py tests/test_definition_xml.py tests/fixtures/xml
git commit -m "feat: edit_classifications and import_definitions with dry-run previews

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Register the tools, dashboard, manifest, docs

**Files:**
- Modify: `src/archicad_mcp/server.py` (full-mode tools, after `set_element_data` around line 335)
- Modify: `tests/test_tool_annotations.py` (`WRITERS`)
- Modify: `scripts/build_dashboard.py` (`TOOLS`, command-to-tool map), then regenerate `docs/api-dashboard.html`
- Modify: `manifest.json` (`tools`)
- Modify: `README.md`, `docs/index.md` (tool list, next to `set_element_data`)

**Interfaces:**
- Consumes: `edit_property_definitions`, `edit_classifications`, `import_definitions`.

- [ ] **Step 1: Failing test.** Add the three names to `WRITERS` in `tests/test_tool_annotations.py`:

```python
    "run_script", "apply_changeset",
    "edit_property_definitions", "edit_classifications", "import_definitions",
```

Run: `uv run pytest tests/test_tool_annotations.py tests/test_manifest.py -v`
Expected: FAIL (`WRITERS names tools that do not exist`).

- [ ] **Step 2: Register** in `_register_full_mode_tools`, after `set_element_data`:

```python
    from archicad_mcp.core import classification_edit as _classification_edit
    from archicad_mcp.core import definition_edit as _definition_edit

    @mcp.tool(description=(
        "Edit custom property DEFINITIONS in place (not element values): name, "
        "description, group, default value or expressions, availability, enum "
        "options. DRY-RUN BY DEFAULT: returns each property's before/after and "
        "warnings; pass dry_run=false to commit. Address properties as 'Group/Name' "
        "(search_definitions) or GUID; availability entries as 'System/Code', with "
        "'/*' for the item and everything below it; enum options by their text. "
        "Example change: {\"property\": \"Office/Status\", \"name\": \"Approval\", "
        "\"availability\": {\"add\": [\"Uniclass/Ss_25/*\"]}, \"enum\": {\"rename\": "
        "{\"Old\": \"New\"}, \"remove\": [\"X\"], \"add\": [\"Y\"], \"order\": [...]}, "
        "\"default\": \"New\"}. GUIDs are kept, so element values survive everything "
        "except removing an option or availability. Needs the Tapir build with "
        "UpdateClassificationItems."),
        **_tool_meta("Edit property definitions", read_only=False, destructive=True))
    @_guarded
    def edit_property_definitions(changes: list[dict], dry_run: bool = True,
                                  port: int | None = None) -> dict:
        return _definition_edit.edit_property_definitions(_conn(port), changes, dry_run)

    @mcp.tool(description=(
        "Edit classification systems and items in place. DRY-RUN BY DEFAULT; pass "
        "dry_run=false to commit. Items: {\"item\": \"System/Code\", \"code\"?, "
        "\"name\"?, \"description\"?}. Systems: {\"system\": \"Name\", \"name\"?, "
        "\"description\"?, \"source\"?, \"version\"?, \"date\"? (YYYY-MM-DD)}. Items "
        "keep their GUID, so classified elements and property availability stay "
        "attached. Moving an item to another parent is not supported."),
        **_tool_meta("Edit classifications", read_only=False, destructive=True))
    @_guarded
    def edit_classifications(changes: list[dict], dry_run: bool = True,
                             port: int | None = None) -> dict:
        return _classification_edit.edit_classifications(_conn(port), changes, dry_run)

    @mcp.tool(description=(
        "Import a Property Manager (kind='property') or Classification Manager "
        "(kind='classification') XML file. DRY-RUN BY DEFAULT: lists what is new and "
        "what collides with existing names, and what the conflict policy does. "
        "conflict: property append|replace|skip; classification merge|replace|skip "
        "(item_conflict replace|skip). Commit returns what was created and removed."),
        **_tool_meta("Import definitions XML", read_only=False, destructive=True))
    @_guarded
    def import_definitions(kind: str, xml_path: str, conflict: str,
                           item_conflict: str = "skip", dry_run: bool = True,
                           port: int | None = None) -> dict:
        return _classification_edit.import_definitions(
            _conn(port), kind, xml_path, conflict, item_conflict, dry_run)
```

- [ ] **Step 3: Dashboard.** In `scripts/build_dashboard.py` add to the command map:

```python
    "UpdatePropertyDefinitions": ["edit_property_definitions"],
    "UpdatePropertyGroups": [],
    "UpdateClassificationSystems": ["edit_classifications"],
    "UpdateClassificationItems": ["edit_classifications"],
    "ImportPropertiesXml": ["import_definitions"],
    "ImportClassificationsXml": ["import_definitions"],
    "GetAllProperties": ["search_definitions", "edit_property_definitions", "import_definitions"],
```

(the last line replaces the existing `GetAllProperties` entry), and to `TOOLS` after `set_element_data`:

```python
    {"name": "edit_property_definitions", "cat": "Definitions", "mode": "full", "mutates": True,
     "desc": "Edit custom property definitions in place: name, group, default, availability, enum options. Dry-run by default."},
    {"name": "edit_classifications", "cat": "Definitions", "mode": "full", "mutates": True,
     "desc": "Edit classification systems and items in place: code, name, description, version. Dry-run by default."},
    {"name": "import_definitions", "cat": "Definitions", "mode": "full", "mutates": True,
     "desc": "Import a Property or Classification Manager XML with a conflict policy. Dry-run by default."},
```

If `"Definitions"` is not an existing category, use the category `search_definitions` is in. Regenerate: `uv run python scripts/build_dashboard.py`.

- [ ] **Step 4: Manifest.** In `manifest.json` `tools`, after `set_element_data`:

```json
    {
      "name": "edit_property_definitions",
      "description": "Edit custom property definitions in place. Dry-run by default."
    },
    {
      "name": "edit_classifications",
      "description": "Edit classification systems and items in place. Dry-run by default."
    },
    {
      "name": "import_definitions",
      "description": "Import Property or Classification Manager XML. Dry-run by default."
    },
```

- [ ] **Step 5: README and docs/index.md.** Next to the `set_element_data` line in each, add one line per tool using the manifest descriptions, plus one sentence: "Needs a Tapir add-on that has `UpdateClassificationItems`; older Tapir versions get a plain refusal and nothing is sent."

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -q`
Expected: all pass (live tests deselected by default).

- [ ] **Step 7: Commit**

```bash
git add src/archicad_mcp/server.py tests/test_tool_annotations.py scripts/build_dashboard.py docs/api-dashboard.html manifest.json README.md docs/index.md
git commit -m "feat: register edit_property_definitions, edit_classifications, import_definitions

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Live canaries on Oprema-objekti and known issues

**Files:**
- Modify: `tests/test_live.py`
- Modify: `docs/known-issues.md`

**Interfaces:**
- Consumes: the three tool functions; `ARCHICAD_MCP_LIVE_PORT=19724`.

- [ ] **Step 1: Canaries.** Append to `tests/test_live.py`, using the module-scoped `conn` fixture already in that file (it reads `ARCHICAD_MCP_LIVE_PORT`; the module is marked `live`). Each canary restores what it changed in a `finally`:

```python
def test_live_rename_property_and_back(conn):
    from archicad_mcp.core.definition_edit import edit_property_definitions
    try:
        r = edit_property_definitions(conn, [{"property": "MCP Test/Fire Rating",
                                                   "name": "Fire Rating Canary"}], dry_run=False)
        assert r["applied"][0]["now"]["name"] == "Fire Rating Canary"
    finally:
        edit_property_definitions(conn, [{"property": "MCP Test/Fire Rating Canary",
                                               "name": "Fire Rating"}], dry_run=False)


def test_live_availability_branch_and_restore(conn):
    from archicad_mcp.core.definition_edit import Definitions, edit_property_definitions
    before = Definitions.load(conn).by_address["MCP Test/Fire Rating"].availability
    try:
        r = edit_property_definitions(conn, [{"property": "MCP Test/Fire Rating",
            "availability": {"add": ["MCP Test/Building/*"]}}], dry_run=False)
        assert "failed" not in r
    finally:
        edit_property_definitions(conn, [{"property": "MCP Test/Fire Rating",
            "availability": {"set": before}}], dry_run=False)
    after = Definitions.load(conn).by_address["MCP Test/Fire Rating"].availability
    assert sorted(after) == sorted(before)


def test_live_classification_code_and_back(conn):
    from archicad_mcp.core.classification_edit import edit_classifications
    try:
        r = edit_classifications(conn, [{"item": "MCP Test/Site", "code": "Site-Canary"}],
                                 dry_run=False)
        assert r["applied"] == ["MCP Test/Site"]
    finally:
        edit_classifications(conn, [{"item": "MCP Test/Site-Canary", "code": "Site"}],
                             dry_run=False)
```

`availability.set` takes addresses; `before` holds GUIDs, which `ClassificationIndex.resolve` accepts, so the restore works.

- [ ] **Step 2: Run them (Oprema-objekti open, new build installed)**

Run: `ARCHICAD_MCP_LIVE_PORT=19724 uv run pytest -m live -k "live_rename or live_availability or live_classification" -v`
Expected: 3 PASS; afterwards Property Manager and Classification Manager in Archicad show the original names and codes.

- [ ] **Step 3: Known issues.** Replace the section "Writing enum properties is not supported" heading's neighbour list by adding a new section after it:

```markdown
## Editing definitions needs the new Tapir commands

`edit_property_definitions`, `edit_classifications` and `import_definitions`
need a Tapir add-on that has `UpdateClassificationItems`. Tapir 1.5.4 to 1.5.9
upstream accept `UpdatePropertyDefinitions` but only for expressions and adding
enum values, and silently ignore the other fields, so the tools check for the
new command first and send nothing without it.

Verified live on AC 29 (2026-09-25, Oprema-objekti):

- Removing an enum option: <what the Info Box showed on the element that held it, from Task 7 Step 4>.
- Import with `replace`: <whether the property GUID survived, from Task 7 Step 4>.
- Teamwork: not yet verified. Archicad answers `APIERR_NOACCESSRIGHT` per item
  when the user lacks the right; which Teamwork right that is has not been
  checked on a real project.
```

Fill both `<...>` from the Task 7 Step 4 record before committing; the placeholder text must not be committed. Update `POLICY_EFFECT["replace"]` in `classification_edit.py` to state the verified result instead of pointing at this page.

- [ ] **Step 4: Run the full suite again** `uv run pytest -q`. Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_live.py docs/known-issues.md src/archicad_mcp/core/classification_edit.py
git commit -m "test: live canaries for definition editing; record the verified behaviour

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: Hand back

- [ ] **Step 1:** Run `uv run pytest -q` and `git log --oneline main..` in the MCP repo, and `git log --oneline origin/main..feature/property-classification-editing` in the Tapir clone. Report both to Aleš.
- [ ] **Step 2:** Ask Aleš, separately, before each of these (none happens without his yes): merge `feature/definition-editing` into `main` and release v0.7.0 through the existing `publish.yml` flow; the Teamwork check on CVP; forking Tapir on GitHub and opening the upstream PR from `feature/property-classification-editing`.
