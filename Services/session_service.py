from config import (
    MAX_PRODUCTS
)

def store_product(session, url, ai_context):

    products = session["products"]

    products[url] = ai_context

    while len(products) > MAX_PRODUCTS:

        for key in list(products):

            if key != session["active_url"]:

                del products[key]

                break

        else:

            break

def _format_price(ctx):
    """İndirimli fiyat varsa onu, yoksa liste fiyatını kısa metinle döndürür."""
    discount = ctx.get("discount_price")
    price = ctx.get("price")

    if discount:
        if price and price != discount:
            return f"{discount} TL (indirimli, liste {price} TL)"
        return f"{discount} TL"

    if price:
        return f"{price} TL"

    return "bilgi yok"


def _stock_summary(ctx):
    """Stok durumunu kompakt özetler: tamamen ve kısmen tükenen renkler.

    Eski davranış tüm stok matrisini JSON olarak gönderiyordu (renk×beden×adet).
    Model için gereken bilgi 'hangi renk/beden mevcut, ne tükendi' olduğundan
    yalnızca tükenenler özetlenir; tam adetler bırakılır (token tasarrufu).
    """
    fully_out = []
    partial = []

    for variant in ctx.get("variants") or []:

        color = variant.get("color") or "-"
        sizes = variant.get("sizes") or {}

        out_sizes = [
            size for size, count in sizes.items()
            if not count or count <= 0
        ]

        if sizes and len(out_sizes) == len(sizes):
            fully_out.append(color)
        elif out_sizes:
            partial.append(f"{color}: {', '.join(out_sizes)} yok")

    parts = []

    if fully_out:
        parts.append("tükenen renkler: " + ", ".join(fully_out))

    if partial:
        parts.append("kısmi: " + "; ".join(partial[:8]))

    if not parts:
        return "tüm renk/beden kombinasyonları stokta"

    return " | ".join(parts)


def _compact_product(ctx, header):
    """Bir ürünü modele yetecek kadar bilgiyle kısa metin olarak biçimler."""
    lines = [f"{header} {ctx.get('name', '')}".strip()]
    lines.append(f"Fiyat: {_format_price(ctx)}")

    colors = ctx.get("available_colors") or []
    sizes = ctx.get("available_sizes") or []

    if colors:
        lines.append("Renkler: " + ", ".join(colors))

    if sizes:
        lines.append("Bedenler: " + ", ".join(sizes))

    lines.append("Stok: " + _stock_summary(ctx))

    return "\n".join(lines)


def build_products_block(session):

    products = session["products"]
    active_url = session["active_url"]

    lines = []

    active_context = products.get(active_url)

    if active_context:
        lines.append(_compact_product(active_context, "AKTİF ÜRÜN —"))

    others = [
        _compact_product(ctx, "—")
        for url, ctx in products.items()
        if url != active_url
    ]

    if others:
        lines.append("\nDİĞER ÜRÜNLER:")
        lines.extend(others)

    return "\n".join(lines)
