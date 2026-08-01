#!/bin/sh
# Konteyner başlangıcı: önce şema migration'ı (additive, idempotent), sonra app.
# MySQL depends_on: service_healthy ile hazır olduğundan burada beklemeye gerek yok.
set -e

echo "▶ Multi-tenant migration (apply) çalıştırılıyor…"
# Migration başarısız olsa bile (ör. geçici DB sorunu) app'i başlatmayı dene;
# apply idempotenttir ve bir sonraki başlangıçta tekrar denenir.
python -m migrations.run apply --tenant-name "${DEFAULT_TENANT_NAME:-Mumi}" \
  --ig-account-id "${IG_ACCOUNT_ID:-}" || echo "⚠ migration uyarısı (devam ediliyor)"

echo "▶ Uygulama başlatılıyor…"
exec uvicorn main:app --host 0.0.0.0 --port 8000
