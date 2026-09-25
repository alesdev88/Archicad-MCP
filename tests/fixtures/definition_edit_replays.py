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
            group_guid=None, description="", default_ids=None):
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
    if default_ids is not None:
        p["defaultEnumValueIds"] = [{"guid": g} for g in default_ids]
    return p


ALL_PROPERTIES = {
    "properties": [
        _custom("p-code", "ELEA", "Sifra", default="/", availability=["i-40", "i-40-10"]),
        _custom("p-cat", "ELEA", "Kategorija", collection="SingleChoiceEnumeration",
                enum=[("e-k", "Kuhinja"), ("e-s", "Sanitarije"), ("e-o", "Staro")],
                default="Kuhinja", availability=["i-40"], default_ids=["e-k"]),
        _custom("p-dup", "ELEA", "Dvojnik", collection="SingleChoiceEnumeration",
                enum=[("e-a", "A"), ("e-a2", "A"), ("e-b", "B")], default="A",
                default_ids=["e-a"]),
        _custom("p-multi", "ELEA", "Vec", collection="MultipleChoiceEnumeration",
                enum=[("m-a", "A"), ("m-b", "B"), ("m-c", "C")], default="A; B",
                default_ids=["m-a", "m-b"]),
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


# ---------- live shapes (AC 29, property-classification-editing build, 2026-09-25) ----------
# Recorded on the MCP-Test project: the "MCP Test" group and system only, plus one
# built-in property and group so both kinds are covered.

LIVE_GET_ALL_PROPERTIES = {'properties': [{'propertyId': {'guid': '5A6C957A-0B61-4E4E-9BAB-A03BF9278B38'},
                 'propertyType': 'Custom',
                 'propertyGroupName': 'MCP Test',
                 'propertyName': 'Fire Rating',
                 'propertyCollectionType': 'Single',
                 'propertyValueType': 'String',
                 'propertyMeasureType': 'Default',
                 'propertyIsEditable': True,
                 'isExpressionBased': False,
                 'propertyGroupId': {'guid': 'DA4D5B3B-000B-3142-9511-F633B9C9151B'},
                 'propertyDescription': '',
                 'defaultValueDisplay': '-',
                 'availability': [{'classificationItemId': {'guid': '4C55826D-C08A-724E-88D4-D2B92E1D1338'}},
                                  {'classificationItemId': {'guid': 'CFEC33A3-91B5-8842-A494-885B781E07B3'}}]},
                {'propertyId': {'guid': '655E3DAE-046B-3C4B-A8F2-F494A7DFAFBB'},
                 'propertyType': 'Custom',
                 'propertyGroupName': 'MCP Test',
                 'propertyName': 'Canary Enum',
                 'propertyCollectionType': 'SingleChoiceEnumeration',
                 'propertyValueType': 'String',
                 'propertyMeasureType': 'Default',
                 'propertyIsEditable': True,
                 'isExpressionBased': False,
                 'propertyGroupId': {'guid': 'DA4D5B3B-000B-3142-9511-F633B9C9151B'},
                 'propertyDescription': 'enum-removal probe',
                 'defaultValueDisplay': 'Keep',
                 'availability': [{'classificationItemId': {'guid': '7054B35D-A76D-0242-AA39-4F9BDA13F40E'}}],
                 'possibleEnumValues': [{'enumValue': {'displayValue': 'Keep',
                                                       'guid': 'CFA853E3-02C5-F249-89CF-8953FF14512C'}}]},
                {'propertyId': {'guid': '7B8A48A1-A078-4E58-A2B0-0F82AA9A5EAD'},
                 'propertyType': 'StaticBuiltIn',
                 'propertyGroupName': 'Window/Door',
                 'propertyName': 'W/D Opening Volume (Archicad 20)',
                 'propertyCollectionType': 'Single',
                 'propertyValueType': 'Real',
                 'propertyMeasureType': 'Volume',
                 'propertyIsEditable': False,
                 'isExpressionBased': False,
                 'propertyGroupId': {'guid': '572F56FE-5F89-4357-A9C3-C4FBB16D7ED4'},
                 'propertyDescription': ''}],
 'propertyGroups': [{'propertyGroupId': {'guid': 'DA4D5B3B-000B-3142-9511-F633B9C9151B'},
                     'name': 'MCP Test',
                     'description': '',
                     'isCustom': True},
                    {'propertyGroupId': {'guid': '572F56FE-5F89-4357-A9C3-C4FBB16D7ED4'},
                     'name': 'Window/Door',
                     'description': '',
                     'isCustom': False}]}

LIVE_CLASSIFICATION_SYSTEMS = {'classificationSystems': [{'classificationSystemId': {'guid': '9E51DF4F-D453-E543-9BC6-A7628632EC38'},
                            'name': 'MCP Test',
                            'description': 'Test system for archicad-mcp live canaries',
                            'source': '',
                            'version': '1',
                            'date': '2026-09-25'}]}

LIVE_CLASSIFICATION_TREE = {'classificationItems': [{'classificationItem': {'classificationItemId': {'guid': '7224B8EE-7E2B-D949-95B3-1FD6AC18E548'},
                                                 'id': 'Building',
                                                 'name': 'Building',
                                                 'description': '',
                                                 'children': [{'classificationItem': {'classificationItemId': {'guid': '4C55826D-C08A-724E-88D4-D2B92E1D1338'},
                                                                                      'id': 'Wall',
                                                                                      'name': 'Wall',
                                                                                      'description': ''}},
                                                              {'classificationItem': {'classificationItemId': {'guid': '7054B35D-A76D-0242-AA39-4F9BDA13F40E'},
                                                                                      'id': 'Slab',
                                                                                      'name': 'Slab',
                                                                                      'description': ''}},
                                                              {'classificationItem': {'classificationItemId': {'guid': 'CFEC33A3-91B5-8842-A494-885B781E07B3'},
                                                                                      'id': 'Object',
                                                                                      'name': 'Object',
                                                                                      'description': ''}}]}},
                         {'classificationItem': {'classificationItemId': {'guid': 'C2E32E45-C556-7643-A80B-26D9088D6CFE'},
                                                 'id': 'Site',
                                                 'name': 'Site',
                                                 'description': ''}}]}

LIVE_UPDATE_OK = {'executionResults': [{'success': True}]}

LIVE_UPDATE_FAILED = {'executionResults': [{'error': {'code': -2130313112,
                                 'message': 'built-in properties cannot be changed'},
                       'success': False}]}

LIVE_IMPORT_RESPONSE = {'executionResult': {'success': True}, 'created': [], 'removed': []}

