"""
Model maliyet karşılaştırması — "4o-mini yerine 4o kullansaydık ne kadar tutardı?"

usage_logs tablosundaki GERÇEK token verisine bakar; mevcut modelle (gpt-4.1-mini)
alternatif modeller arasındaki maliyet farkını hesaplar. Hiçbir şeyi değiştirmez,
yalnızca okur ve rapor basar.

Çalıştırma (proje klasöründe, MySQL erişilebilir bir makinede):
    python model_cost_compare.py

.env dosyasındaki MYSQL_* ve MODEL_NAME değerlerini kullanır.
"""

import os
from dotenv import load_dotenv
import mysql.connector

load_dotenv()

# ----------------------------------------------------------------------
# Fiyatlar — USD / 1.000.000 token  (giriş, çıkış)
# Kaynak: OpenAI API pricing 2026. Gerekirse buradan güncelle.
# ----------------------------------------------------------------------
PRICES = {
    "gpt-4.1-mini": (0.40, 1.60),   # ŞU ANKİ MODEL
    "gpt-4o-mini":  (0.15, 0.60),
    "gpt-4o":       (2.50, 10.00),
    "gpt-4.1":      (2.00, 8.00),
    "gpt-4.1-nano": (0.10, 0.40),
}

CURRENT_MODEL = os.getenv("MODEL_NAME") or "gpt-4.1-mini"


def cost_for(model, prompt_tokens, completion_tokens):
    """Verilen token sayıları için modelin maliyetini USD döndürür."""
    inp, out = PRICES[model]
    return prompt_tokens / 1_000_000 * inp + completion_tokens / 1_000_000 * out


def get_usd_try():
    """Güncel USD/TRY kuru (projedeki currency_service ile aynı kaynak)."""
    try:
        import requests
        r = requests.get("https://open.er-api.com/v6/latest/USD", timeout=5)
        r.raise_for_status()
        return r.json()["rates"]["TRY"]
    except Exception:
        return None


def fetch_usage():
    """usage_logs'tan toplamları ve model bazlı kırılımı çeker."""
    conn = mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
        database=os.getenv("MYSQL_DATABASE", "instaagent"),
    )
    cur = conn.cursor()

    cur.execute("""
        SELECT
            COUNT(*),
            COALESCE(SUM(prompt_tokens), 0),
            COALESCE(SUM(completion_tokens), 0),
            COALESCE(SUM(cost), 0),
            MIN(timestamp),
            MAX(timestamp)
        FROM usage_logs
    """)
    total = cur.fetchone()

    cur.execute("""
        SELECT
            model,
            COUNT(*),
            COALESCE(SUM(prompt_tokens), 0),
            COALESCE(SUM(completion_tokens), 0),
            COALESCE(SUM(cost), 0)
        FROM usage_logs
        GROUP BY model
        ORDER BY SUM(cost) DESC
    """)
    by_model = cur.fetchall()

    cur.close()
    conn.close()
    return total, by_model


def main():
    total, by_model = fetch_usage()
    req_count, p_tok, c_tok, logged_cost, first_ts, last_ts = total
    p_tok, c_tok, logged_cost = int(p_tok), int(c_tok), float(logged_cost)

    if req_count == 0:
        print("usage_logs boş — henüz kayıtlı AI isteği yok.")
        return

    print("=" * 64)
    print("  MODEL MALİYET KARŞILAŞTIRMASI")
    print("=" * 64)
    print(f"Kayıt aralığı : {first_ts}  →  {last_ts}")
    print(f"Toplam istek  : {req_count:,}")
    print(f"Giriş token   : {p_tok:,}")
    print(f"Çıkış token   : {c_tok:,}")
    print(f"Toplam token  : {p_tok + c_tok:,}")
    print(f"Loglanan maliyet (DB): ${logged_cost:,.4f}")
    print(f"Aktif model (.env)   : {CURRENT_MODEL}")

    print("\n--- Model bazlı kırılım (kayıtlı) ---")
    for m, n, pt, ct, cst in by_model:
        print(f"  {m:<16} istek={n:<6} giriş={int(pt):>10,} çıkış={int(ct):>10,} maliyet=${float(cst):,.4f}")

    usd_try = get_usd_try()

    # Mevcut modelin token bazlı yeniden hesaplanmış maliyeti (referans)
    base_model = CURRENT_MODEL if CURRENT_MODEL in PRICES else "gpt-4.1-mini"
    base_cost = cost_for(base_model, p_tok, c_tok)

    print("\n" + "=" * 64)
    print(f"  ALTERNATİF MODELLER  (aynı {p_tok + c_tok:,} token ile)")
    print("=" * 64)
    header = f"{'Model':<16}{'Maliyet (USD)':>16}{'Fark (USD)':>16}{'Kat':>8}"
    print(header)
    print("-" * len(header))

    for model in ["gpt-4.1-mini", "gpt-4o-mini", "gpt-4.1", "gpt-4o"]:
        c = cost_for(model, p_tok, c_tok)
        diff = c - base_cost
        mult = (c / base_cost) if base_cost else 0
        star = "  ← şu an" if model == base_model else ""
        print(f"{model:<16}{c:>16.4f}{diff:>+16.4f}{mult:>7.2f}x{star}")

    # Asıl soru: gpt-4o'ya geçseydik EK maliyet
    o4_cost = cost_for("gpt-4o", p_tok, c_tok)
    extra_usd = o4_cost - base_cost

    print("\n" + "=" * 64)
    print("  SONUÇ:  gpt-4o'ya geçseydik")
    print("=" * 64)
    print(f"  Mevcut ({base_model}) : ${base_cost:,.4f}")
    print(f"  gpt-4o toplam         : ${o4_cost:,.4f}")
    print(f"  EK MALİYET            : ${extra_usd:,.4f}  (+%{(extra_usd/base_cost*100):.0f})")
    if usd_try:
        print(f"  EK MALİYET (TL)       : ₺{extra_usd * usd_try:,.2f}   (kur: {usd_try:.2f})")
    print("\nNot: Hesap tüm token'ları tam ücretten sayar. Prompt caching ile")
    print("gerçek gpt-4o maliyeti bir miktar daha düşük olabilir.")


if __name__ == "__main__":
    main()
