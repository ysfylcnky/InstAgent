# gpt-4o Test Senaryoları — Noure Prive Instagram Botu

Model `gpt-4o`'ya geçirildi (`.env` → `MODEL_NAME=gpt-4o`, `config.py` fiyatları 2.50/10.00). Etkili olması için **uvicorn'u yeniden başlat**.

## Nasıl kullanılır

Aşağıdaki mesajları sırayla bota (Instagram DM'den ya da test hesabından) gönder ve **Beklenen davranış** ile karşılaştır. Aynı senaryoları eski modelde (gpt-4.1-mini) de dener, farkı not edersen 4o'nun gerçekten değer katıp katmadığını görürsün. Özellikle dikkat: **doğru ürün eşleşmesi, uydurma yapmama (halisinasyon), tutarlı çok adımlı sipariş akışı ve ton**.

Kullanılan gerçek ürünler: `TENSEL CUPRA PANTOLON`, `Yeni Sezon Marya Elbise`, `Yeni Sezon Seher Çilek Kap`, `Yeni Sezon Tasarım Tunik`, `Yeni Sezon Kat Kat Etek`, `Noi Sunset Elbise`.

---

## BÖLÜM 1 — Uçtan uca kabul testi (her özellik bir kez)

### 1.1 Karşılama
- [ ] Mesaj: `Merhaba`
- Beklenen: Kısa, sıcak karşılama; ürün/yardım yönlendirmesi. Geçmiş sıfırlanır.

### 1.2 İsimle ürün arama (tam ad)
- [ ] Mesaj: `Tensel cupra pantolon fiyatı ne kadar?`
- Beklenen: `urun_ara` çağrılır, ürün aktifleşir; fiyat + renk/beden bilgisi döner. Renkler siyah/haki/bej/mürdüm/lacivert/mavi/beyaz/kahve arasında olmalı.

### 1.3 Tek kelime / kısa ad ile arama
- [ ] Mesaj: `panço var mı`
- Beklenen: Kısa ada rağmen `urun_ara` çağrılır; "bilgim yok" DEMEZ. Panço kategorisinden ürün(ler) döner ya da netleştirme sorar.

### 1.4 Aktif üründe renk / beden / stok sorusu
- [ ] Önce: `Marya elbise`
- [ ] Sonra: `40 beden mavi var mı?`
- Beklenen: Aktif ürün Marya Elbise; bedenler 38-44, renkler beyaz/sarı/mavi/vizon. Mavi + 40 için stok durumunu doğru söyler.

### 1.5 Ürün linki gönderme
- [ ] Mesaj: `https://noureprive.com/tensel-cupra-pantolon`
- Beklenen: Linkten ürünü bulur, "ürünü görüntüledim" tarzı yanıt; sonra sorulara açık.

### 1.6 Sipariş oluşturma — Kapıda Ödeme (tam akış)
- [ ] `Seher çilek kap almak istiyorum`
- [ ] `42 beden, bej renk`
- [ ] `Ayşe Yılmaz, 0555 123 45 67, Kadıköy Moda Cad. No:5 D:3 İstanbul`
- [ ] `Kapıda ödeme`
- [ ] Bot özet verip onay isteyince: `Evet onaylıyorum`
- Beklenen: **Onaydan ÖNCE** `siparis_olustur` çağrılmaz; önce özet + onay ister. Onaydan sonra sipariş oluşur, kapıda ödeme +90 TL ek ücret notu, mağazaya bildirim gider.

### 1.7 Sipariş — Havale/EFT + dekont
- [ ] `Tensel cupra pantolon siyah L, kapıda değil havale ile alayım`
- [ ] Ad/adres/telefon verip onayla
- [ ] Bot IBAN verince: `Ödemeyi yaptım, dekont atıyorum`
- [ ] (Ardından bir görsel/dekont gönder)
- Beklenen: Havale seçilince IBAN paylaşılır, `order_state=odeme_bekliyor`. "Ödedim" + görsel gelince sipariş kapatılır ("dekontunuz elimize ulaştı").

### 1.8 Sipariş güncelleme
- [ ] 1.6'daki siparişten sonra: `Adresi değiştireyim, Beşiktaş Barbaros Bulvarı No:12 olsun`
- Beklenen: `siparis_guncelle` çağrılır; sadece adres değişir, diğer alanlar korunur; mağazaya "SİPARİŞ GÜNCELLEME" bildirimi.

### 1.9 Farklı ürüne geçiş
- [ ] Aktif ürün pantolonken: `Marya elbiseye bakabilir miyim`
- Beklenen: Reddetmeden yeni ürüne geçer, elbise bilgisi döner.

### 1.10 Sesli mesaj
- [ ] Bir sesli mesaj gönder (ör. "Tunik fiyatı ne kadar" diye)
- Beklenen: Ses yazıya çevrilir (Whisper), metin gibi işlenir.

### 1.11 Görsel (bağlam dışı)
- [ ] Sipariş/ödeme akışı yokken bir görsel gönder
- Beklenen: "Şu an yazılı ve sesli mesajları yanıtlayabiliyorum" tarzı nazik yanıt.

### 1.12 Konu dışı genel soru
- [ ] Mesaj: `Kargo kaç günde gelir?`
- Beklenen: Ürün aramaya zorlamadan, mağaza bilgisi çerçevesinde makul yanıt (prompt'a göre).

---

## BÖLÜM 2 — Zor / kenar durumlar (4o'yu zorlayan)

### 2.1 Yazım hatalı ürün adı
- [ ] Mesaj: `tensl cupra pantol fiyat`
- Beklenen: Yazım hatasına rağmen doğru ürünü bulur. (mini burada sık başarısız olur — kritik karşılaştırma noktası.)

### 2.2 Belirsiz ad → çoklu aday → numarayla seçim
- [ ] `elbise arıyorum`
- Beklenen: Birden fazla elbise (Marya, Noi Sunset...) numaralı liste olarak sunulur.
- [ ] Sonra: `1`
- Beklenen: 1. üründeki elbise aktifleşir.

### 2.3 Yanlış seçim → düzeltme
- [ ] 2.2'de liste geldikten sonra: `yok pardon 2. olan`
- Beklenen: "2 numaralı ürüne geçiyorum" deyip doğru ürüne geçer.

### 2.4 Olmayan ürün — halisinasyon tuzağı
- [ ] Mesaj: `Erkek gömleği var mı?`
- Beklenen: Bulamaz, uydurmaz; nazikçe kadın tesettür ürünleri sattıklarını / ürün adını netleştirmeyi ister. **Sahte ürün/fiyat üretmemeli.**

### 2.5 Üründe olmayan renk isteme
- [ ] `Marya elbise` → sonra `kırmızı var mı?`
- Beklenen: Marya'da renkler beyaz/sarı/mavi/vizon; kırmızı YOK. Bot "kırmızı yok" deyip mevcut renkleri sunar, **uydurmaz.**

### 2.6 Ödeme beklerken farklı ürün sorma
- [ ] Havale siparişi `odeme_bekliyor` durumdayken: `bu arada tunik de var mı?`
- Beklenen: Siparişi iptal etmeden `urun_ara` ile tunik'i arar; "yardımcı olamam" demez.

### 2.7 Parça parça sipariş bilgisi (çok adımlı hafıza)
- [ ] `Kat kat etek istiyorum` → `krem renk` → `M beden` → `havale` → ad/adres/telefon → onay
- Beklenen: Bilgileri adım adım toplar, önceki adımları unutmaz, en sonda eksiksiz özet + onay.

### 2.8 Eksik bilgiyle sipariş zorlaması
- [ ] `Hemen sipariş ver` (hiç ürün/beden/adres yokken)
- Beklenen: Eksik alanları tek tek sorar; **boş/uydurma alanla sipariş oluşturmaz.**

### 2.9 İndirim / kampanya sorusu
- [ ] `İndirim kodu var mı?`
- Beklenen: Prompt'ta tanımlıysa doğru bilgi; tanımlı değilse uydurmadan "kampanyaları takip edin" tarzı yanıt. (Sitede `noure100` var — bot bunu bilmiyorsa uydurmamalı.)

### 2.10 Aynı anda iki ürün
- [ ] `Hem tunik hem pantolon fiyatı lazım`
- Beklenen: İkisini de mantıklı şekilde ele alır ya da sırayla netleştirir; birini yok saymaz.

### 2.11 Karışık dil
- [ ] `Do you have this in size L? Marya elbise`
- Beklenen: Türkçe/İngilizce karışık mesajı anlar, doğru ürün + beden yanıtı.

### 2.12 Beden danışma (akıl yürütme — 4o'nun parladığı yer)
- [ ] `1.68 boyum 62 kiloyum, pantolonda hangi beden?`
- Beklenen: Genel beden tablosuna göre makul öneri (ör. M/L), kesin/tıbbi iddia yok, "kesin oturur" garantisi vermez.

### 2.13 Kapıda ödeme ek ücret
- [ ] `Kapıda ödemede ekstra ücret var mı?`
- Beklenen: +90 TL ek ücret bilgisini doğru verir.

### 2.14 Onaydan önce sipariş oluşturmama
- [ ] Tüm bilgileri ver ama **onaylama**, `Bir düşüneyim` de
- Beklenen: `siparis_olustur` çağrılmaz; özet durur, baskı yapmaz.

### 2.15 Alakasız / spam
- [ ] `asdfgh 123 selam napıyon`
- Beklenen: Kibar, kısa; ürün/sipariş akışına nazikçe yönlendirir, saçmalamaz.

---

## Puanlama (opsiyonel)

Her senaryo için: ✅ doğru · ⚠️ kısmen · ❌ hatalı. Özellikle 2.1, 2.4, 2.5, 2.8, 2.14'te mini vs 4o farkına bak — bu beş senaryo modelin gerçekten "daha yeterli" olup olmadığını en net gösterenler.
