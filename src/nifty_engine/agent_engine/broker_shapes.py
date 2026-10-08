"""Normalize observed Groww readback spellings without changing field values."""
import copy


def smart_order(row):
    """The live GTT API can return camelCase fields inside its order object.

    The published schema uses snake_case. Accept either exact spelling, reject
    contradictory duplicates, and leave all identity/price checks to callers.
    No missing values or ownership identifiers are inferred.
    """
    if not isinstance(row, dict):
        raise ValueError('SMART_ORDER_OBJECT_REQUIRED')
    result = copy.deepcopy(row)
    order = result.get('order')
    if isinstance(order, dict):
        for canonical, alias in (('order_type', 'orderType'), ('transaction_type', 'transactionType')):
            if alias not in order:
                continue
            if canonical in order and order[canonical] != order[alias]:
                raise ValueError('SMART_ORDER_FIELD_CONFLICT')
            order[canonical] = order.pop(alias)
    return result
