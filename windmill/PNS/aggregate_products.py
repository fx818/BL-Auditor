def main(parsed_rows: list = []) -> list:
    products = []
    for row in parsed_rows:
        for p in (row.get("products") or []):
            products.append(p)
    return products
