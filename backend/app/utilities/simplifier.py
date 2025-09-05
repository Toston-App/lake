def extract_field(obj, field):
        """Helper to extract field from either dict or model object"""
        if isinstance(obj, dict):
            return obj.get(field)
        return getattr(obj, field, None)

# TODO: Add docstrings and maybe do this directly in the query to avoid the need for this function
def categories(categories):
    """
    Simplifies category objects to include only id, name, and subcategories with id and name.
    Works with both dictionary and SQLAlchemy model objects.

    Args:
        categories: A single category object or a list of category objects

    Returns:
        A simplified category object or list of simplified category objects
    """
    def simplify_category(category):
        """Simplify a single category"""
        subcategories = [
            {"id": extract_field(sub, "id"), "name": extract_field(sub, "name")}
            for sub in extract_field(category, "subcategories") or []
        ]

        return {
            "id": extract_field(category, "id"),
            "name": extract_field(category, "name"),
            "is_income": extract_field(category, "is_income"),
            "subcategories": subcategories
        }

    # Process single item or list
    if not isinstance(categories, list):
        return simplify_category(categories)

    return [simplify_category(category) for category in categories]

# TODO: Add docstrings and maybe do this directly in the query to avoid the need for this function
def places(places):
    """
    Simplifies place objects to include only id, name.
    Works with both dictionary and SQLAlchemy model objects.

    Args:
        places: A single place object or a list of place objects

    Returns:
        A simplified place object or list of simplified place objects
    """
    def simplify_place(place):
        """Simplify a single place"""
        return {
            "id": extract_field(place, "id"),
            "name": extract_field(place, "name"),
        }

    # Process single item or list
    if not isinstance(places, list):
        return simplify_place(places)

    return [simplify_place(place) for place in places]

# TODO: Add docstrings and maybe do this directly in the query to avoid the need for this function
def accounts(accounts):
    """
    Simplifies account objects to include only id, name.
    Works with both dictionary and SQLAlchemy model objects.

    Args:
        accounts: A single account object or a list of account objects

    Returns:
        A simplified account object or list of simplified account objects
    """
    def simplify_account(account):
        """Simplify a single account"""
        return {
            "id": extract_field(account, "id"),
            "name": extract_field(account, "name"),
        }

    # Process single item or list
    if not isinstance(accounts, list):
        return simplify_account(accounts)

    return [simplify_account(account) for account in accounts]
