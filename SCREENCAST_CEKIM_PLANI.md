# Mumio — Screencast Çekim Planı (Meta App Review)

Hedef süre: **2,5–4 dakika**. Tek video, iki izni de kapsıyor.
Kayıt sırasında bu dosyayı ikinci bir cihazda/telefonda açık tut.

---

## A · Kayıttan ÖNCE (sırayla, hepsi zorunlu)

1. **Instagram'dan uygulama iznini kaldır.**
   Instagram (mobil) → Ayarlar ve gizlilik → Web sitesi izinleri → Uygulamalar
   ve web siteleri → **Mumio** → Kaldır.
   *Neden:* İzin duruyorsa Connect'e bastığında onay ekranı gelmez, videonun en
   değerli karesi kaybolur.

2. **Panelden çıkış yap.** `ig.mumifashion.com/logout`
   *Neden:* Giriş sahnesini göstereceğiz.

3. **Ekranı temizle.**
   - Terminal, VS Code, `.env` — hepsini kapat.
   - Tarayıcıda yer imleri çubuğunu gizle (`Ctrl+Shift+B`), kişisel bilgi sızmasın.
   - Gereksiz sekmeleri kapat, bildirimleri sustur (Windows → Odaklanma yardımı).

4. **İki pencereyi yan yana diz — tek monitörde.**

   | | Pencere | İçerik |
   |---|---|---|
   | Sol | Tarayıcı, normal profil | `ig.mumifashion.com` (henüz giriş yok) |
   | Sağ | Tarayıcı, **gizli pencere** veya ikinci profil | `instagram.com`, kişisel hesabınla giriş yapılmış |

   > İki ayrı monitör kullanma. Xbox Game Bar çoklu monitör kaydedemez, OBS
   > gerekir ve kurulum hata payını artırır. Tek ekranda yan yana iki pencere
   > hem daha kolay hem reviewer için daha okunaklı — mesaj ile botun cevabı
   > aynı karede görünür.

5. **Sağ pencerede `mumifashion` sohbetini aç ama mesaj yazma.** Kayıt
   başladığında yazmaya hazır ol.

6. **Kayıt aracı:** `Win+G` (Xbox Game Bar) yeterli. Daha fazla kontrol için OBS
   → Display Capture. 1080p, 30 fps.

---

## B · Sahne sahne

Altyazıları kayıt sırasında değil, **sonradan** ekleyeceksin (bkz. bölüm C).
Aşağıdaki numaralar altyazı kartlarıyla eşleşiyor.

### 1 · Tanıtım sayfası (~5 sn)

**Ekran:** Sol pencere, `ig.mumifashion.com`
**Yap:** Sayfayı aç. Fiyat planlarının göründüğü yere kadar hızlıca kaydır, bir
saniye durakla, sonra yukarı dön.
**Neden:** Reviewer bunun başka mağazalara satılan bir SaaS olduğunu görmeli.
Advanced Access "birden fazla işletmeye hizmet eden Tech Provider" için veriliyor.

### 2 · Panele giriş (~10 sn)

**Ekran:** Sol pencere
**Yap:** Sağ üstten **Giriş Yap** → `metareview@mumifashion.com` / `Meta.Test44`
→ gönder.
**Neden:** Submission'da verdiğin kimlik bilgilerinin gerçekten çalıştığını
kanıtlıyorsun. Kendi hesabınla girme.

### 3 · Kurulum ekranı (~5 sn)

**Ekran:** Sol pencere
**Yap:** Bağlantı kesik olduğu için otomatik Kurulum'a yönlendirileceksin.
Yönlendirilmezsen sidebar → **Kurulum**. Instagram bölümüne kaydır.
**Dikkat:** Kırmızı "Eksik" etiketli bir bölümün üzerinde durma, hızlı geç.

### 4 · Connect butonu (~3 sn)

**Ekran:** Sol pencere
**Yap:** **Instagram'ı Bağla · Connect with Instagram** butonunu 2–3 saniye
kadrajda tut, sonra tıkla.
**Neden:** Meta'nın action item'ı: *"Verify that the login button is visible in
your app and screencast."*

### 5 · İzin ekranı (~8 sn) — EN ÖNEMLİ KARE

**Ekran:** Sol pencere, Instagram yetkilendirme sayfası
**Yap:** İzin listesinin göründüğü yerde **dur**. İki izin okunacak kadar bekle:
profil bilgilerini görüntüleme + mesajlaşmayı yönetme. Sonra onayla.
**Neden:** Reviewer talep ettiğin iki izni ve **fazla izin istemediğini** kendi
gözüyle görüyor.

### 6 · Bağlantı tamam (~5 sn)

**Ekran:** Sol pencere, Kurulum sayfasına dönüş
**Yap:** `@mumifashion` kullanıcı adının göründüğü yeri göster.
**Kanıtladığın izin:** `instagram_business_basic`

### 7 · Müşteri DM atıyor (~8 sn)

**Ekran:** Sağ pencere
**Yap:** `mumifashion`'a yaz: **`Bu ürün stokta var mı?`** → gönder.

### 8 · Bot cevaplıyor (~8 sn)

**Ekran:** Sağ pencere
**Yap:** Bekle, cevabın gelişini kes. Hızlandırma, kırpma yapma.
**Kanıtladığın izin:** `instagram_business_manage_messages`

### 9 · Ürün sorgusu (~15 sn)

**Ekran:** Sağ pencere
**Yap:** Kataloğunda gerçekten olan bir ürün adı yaz. Bot fiyat, renk ve beden
döndürsün.
**Neden:** İKAS entegrasyonunu kanıtlar; talimatta yazdığımız akışla örtüşür.

### 10 · Sipariş (~20 sn)

**Ekran:** Sağ pencere
**Yap:** Renk + beden, sonra ad-soyad + adres ver. **`Siparişiniz alındı`**
onayını bekle.

### 11 · Panelde doğrulama (~15 sn)

**Ekran:** Sol pencere
**Yap:** Sidebar → **Konuşmalar** → az önceki sohbeti aç, aynı mesajları göster.
Sonra sidebar → **Müşteriler** → yeni müşteri kaydını göster.
**Neden:** Mağazanın, asistanın kendi adına ne söylediğini denetleyebildiğini
gösterir. Meta otomatik mesajlaşmada bunu bekler.

---

## C · Kayıttan SONRA

1. **Altyazıları ekle.** Clipchamp (Windows 11'de yüklü) → metin katmanı.
   Metinler `APP_REVIEW_SUBMISSION.md` §5'te; sahne numaralarıyla eşleşiyor.
   Her kart 3–4 saniye ekranda kalsın, alt ortada, koyu zemin üstünde beyaz yazı.

2. **Kontrol listesi — video bitmeden önce izle:**
   - [ ] Hiçbir karede token, `.env`, terminal veya gerçek müşteri verisi yok
   - [ ] İzin ekranı okunacak kadar uzun duruyor
   - [ ] Connect butonu net görünüyor
   - [ ] Botun cevabı kesilmeden, gerçek zamanlı görünüyor
   - [ ] Her Türkçe kilit ifadenin İngilizce karşılığı altyazıda var
   - [ ] Süre 2,5–4 dakika arası

3. **Dışa aktar:** MP4, 1080p. Meta'nın yükleme sınırı cömert, sıkıştırmaya gerek yok.

4. **Kare dışa aktar** (bana göstermek için): izin ekranı, altyazılı iki sahne,
   botun cevabı, Konuşmalar sekmesi. PNG olarak kaydet.

---

## D · Kayıttan sonra bağlantıyı bırakma

Video bittiğinde Instagram bağlantısı **kurulu** kalmalı (sahne 5'te yeniden
bağladın, sorun yok). Bir daha kaldırma — reviewer siteye baktığında çalışır
durumda bulmalı.

---

## E · Submission'a geçiş

Video hazır olunca `APP_REVIEW_SUBMISSION.md` dosyasındaki blokları sırayla
panele yapıştır:

| Panel alanı | Dosyadaki bölüm |
|---|---|
| App Verification → Platform Settings | §1 |
| App Verification → Credentials | §2 |
| How will your app use `instagram_business_basic`? | §3 |
| How will your app use `instagram_business_manage_messages`? | §4 |
| Her iki izin için screencast | çektiğin video |

Business Verification hâlâ incelemedeyse gönderimi ona kadar bekletebilirsin;
Advanced Access verification tamamlanmadan onaylanmıyor.
