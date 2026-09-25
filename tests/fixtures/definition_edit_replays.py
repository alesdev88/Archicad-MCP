"""Definition editing fixtures.

Synthetic cases no test model has: two systems where one name is a prefix of
the other, a property group with a slash in its name, an enum with a duplicated
option text. Shapes follow Tapir GetAllProperties with the fields the
property-classification-editing build adds (propertyGroupId, propertyDescription,
defaultValueDisplay, availability, top-level propertyGroups).
"""

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
    Update* Tapir command (default: all succeed)."""
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
