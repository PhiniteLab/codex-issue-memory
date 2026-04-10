# Geliştirme Yol Haritası — codex-issue-memory v0.2

> Bu belge, MCP sunucusunun daha akıllı, daha etkin ve hatalardan giderek daha iyi öğrenen bir yapıya dönüştürülmesi için hazırlanmış kapsamlı geliştirme planıdır.

## Vizyon

`codex-issue-memory`'nin mevcut durumu **iyi mühendislik** üzerine kurulu.
Eksik olan şey **öğrenme döngülerinin tam kapanması**: Sistem çok sayıda sinyal topluyor ama bunların çoğunu geri beslemede kullanmıyor.

Bu yol haritası dört temel ekseni hedefliyor:

1. **Kapalı öğrenme döngüleri** — toplanan her sinyalin ranklama ve strateji kararlarına geri akması
2. **Bağlama duyarlı adaptasyon** — hata ailesi, repo, kullanıcı bazında farklılaşan eşikler ve ağırlıklar
3. **Sessiz geri bildirim yakalama** — sadece explicit feedback değil, örtük sinyaller de (terk edilen öneriler, tekrarlı sorgular)
4. **Operasyonel şeffaflık** — her kararın neden verildiğinin izlenebilmesi

---

## Faz 0 — Temel İyileştirmeler  *(tamamlandı ✅)*

> Mevcut koda minimum müdahale ile en yüksek etki.

### 0.1 Feedback döngüsünü tamamla ✅

**Sorun:** Sadece `fix_verified` ve `false_positive` feedback tipleri, variant istatistiklerini ve strateji posterior'larını güncelliyor. `candidate_accepted` ve `candidate_rejected` tipleri session memory'ye yazılıyor ama variant `success_count`/`reject_count` değerlerini güncellemiyor.

**Çözüm:**
- `FeedbackService._route_learning()` içinde tüm feedback tiplerini variant stat güncellemesine bağla
- `candidate_accepted` → `success_count += 1` (ağırlık: 0.35)
- `candidate_rejected` → `reject_count += 1` (ağırlık: 0.25)
- `fix_verified` → `success_count += 1` (ağırlık: 1.0, mevcut)
- `false_positive` → `reject_count += 1` (ağırlık: 1.0, mevcut)

**Etki:** Variant posterior'ları 3-4x daha hızlı öğrenir; şu anda kaybedilen ~60% feedback sinyali kullanılır hale gelir.

**Dosyalar:** `services/feedback_service.py`, `storage.py`

### 0.2 Variant başarı oranını ranklama özelliği olarak güçlendir ✅

**Sorun:** `support_score` (variant.times_used) ağırlığı 0.02 — bu, variant'ın kanıtlanmış başarı geçmişini neredeyse yok sayıyor.

**Çözüm:**
- `features.py` içinde `proven_score` hesapla: `(success_count + 1) / (times_used + 2)` (Laplace smoothing)
- `DEFAULT_WEIGHTS['proven_score'] = 0.08` olarak ekle
- `support_score` ağırlığını 0.02 → 0.05'e çıkar

**Etki:** Kanıtlanmış çözümler yeni/denenmemiş çözümlerin üstüne çıkar.

**Dosyalar:** `retrieval/features.py`, `retrieval/ranker.py`

### 0.3 Feature-outcome korelasyonu loglama ✅

**Sorun:** Hangi ranking özelliklerinin gerçek kullanıcı kabulüyle korelasyon gösterdiği bilinmiyor. Ağırlıklar deneysel.

**Çözüm:**
- `FeedbackService` içinde, feedback kaydedilirken `retrieval_candidates` tablosundan feature değerlerini çek
- `feature_outcome_log` tablosu ekle: `(feature_name, feature_value, feedback_type, reward, error_family, created_at)`
- `maintenance.py`'ye `analyze-feature-importance` komutu ekle (basit Pearson korelasyonu)

**Etki:** Offline analiz ile hangi özelliklerin işe yaradığı görülür; v0.3'te otomatik ağırlık kalibrasyonu için veri tabanı oluşmaya başlar.

**Dosyalar:** `storage.py` (yeni tablo), `services/feedback_service.py`, `maintenance.py`

---

## Faz 1 — Bağlama Duyarlı Öğrenme  *(tamamlandı ✅)*

> Tekil global eşik/ağırlık yerine hata ailesi ve repo bazında farklılaşma.

### 1.1 Hata ailesi bazında eşik kalibrasyonu ✅

**Sorun:** `match_accept_threshold=0.68`, `match_weak_threshold=0.40`, `ambiguity_margin=0.09` tüm hata aileleri için aynı. `import_error` için yüksek güvenilirlik (0.75+) gerekli iken `generic_runtime_error` için 0.60 yeterli olabilir.

**Çözüm:**
- `calibration_profile.json` yapısını genişlet:
  ```json
  {
    "families": {
      "import_error": {"accept_threshold": 0.75, "weak_threshold": 0.45},
      "tensor_dtype_error": {"accept_threshold": 0.72}
    }
  }
  ```
- `calibrate-thresholds` komutunu feedback verisinden optimal eşikleri otomatik hesaplayacak şekilde güçlendir
- `MatchDecisionPolicy` zaten family bazında `_threshold_bundle()` çekiyor — kalibrasyon verisi dolduğunda otomatik etkinleşir

**Etki:** Hata ailesi başına %8-15 daha doğru karar.

**Dosyalar:** `retrieval/decision.py`, `matching.py`, `maintenance.py`, `calibration_profile.json`

### 1.2 Bağlamsal yarı-ömür (contextual half-life) ✅

**Sorun:** Tüm stratejilerin posterior'ları aynı hızda bozunuyor (`strategy_half_life_days=75`). Aktif repo'lardaki stratejiler daha yavaş, nadir repo'lar daha hızlı bozunmalı.

**Çözüm:**
- `posteriors.py`'de `repo_velocity_multiplier` hesapla: `feedback_count_last_30d / baseline_rate`
- `decay_factor = 0.5 ** (age / (half_life * velocity_multiplier))`
- Aktif repo'larda yarı-ömür efektif olarak uzar; 30 gündür feedback gelmeyen repo'larda kısalır

**Etki:** Aktif projeler daha stabil posterior'lar üretir; ölü projeler daha hızlı sıfırlanır.

**Dosyalar:** `learning/posteriors.py`, `storage.py` (velocity hesabı)

### 1.3 Hata ailesi bazında ranking ağırlıkları ✅

**Sorun:** 23 ranking ağırlığı tüm hata aileleri için aynı. `tensor_dtype_error` için `dense_score` daha önemli olmalı; `import_error` için `lexical_score` daha belirleyici.

**Çözüm:**
- `calibration_profile.json`'a `"weight_overrides"` bölümü ekle
- `HeuristicRanker` başlatılırken hata ailesine göre ağırlık seti yükle
- Faz 0.3'teki feature-outcome verisiyle doldurul

**Etki:** Hata ailesi başına %5-10 ranking doğruluğu artışı.

**Dosyalar:** `retrieval/ranker.py`, `retrieval/features.py`

### 1.4 FP maliyetini asimetrik ağırlıklandır ✅

**Sorun:** `false_positive` ve basit `rejection` posteriora eşit ağırlıkla giriyor. Ancak FP'nin maliyeti çok daha yüksek (kullanıcı güvenini sarsar, zaman kaybettirir).

**Çözüm:**
- `FeedbackService`'de FP reward'unu `-1.0` → `-2.5` olarak ölçekle
- `safe_override.py`'de FP sayısı eşiğini düşür: FP 2+ olan stratejinin promotion'ını tamamen engelle

**Etki:** Sistemin "yanlış öneri yapmamayı" doğru öneri vermekten önce öğrenmesi.

**Dosyalar:** `services/feedback_service.py`, `learning/safe_override.py`

---

## Faz 2 — Sessiz Geri Bildirim ve Session Zekası  *(orta öncelik)*

> Explicit feedback olmadan bile öğrenme.

### 2.1 Örtük reddetme tespiti (implicit rejection)

**Sorun:** Kullanıcı `issue_match` çağırıp sonucu görmezden gelirse (feedback yok) bu "sessiz reddetme" yapıyor. Şu anda bu sinyal tamamen kayıp.

**Çözüm:**
- `retrieval_events` tablosunda `has_feedback` flag'i ekle
- Belirli bir süre sonra (ör. 10 dakika session içinde) feedback gelmemişse `implicit_ignore` olarak işaretle
- `implicit_ignore` → zayıf negatif sinyal (reward: -0.1)
- Session memory'ye "gösterildi ama tıklanmadı" olarak kaydet

**Etki:** Şu anda ~70% kayıp olan "kullanılmayan öneri" sinyalini yakalar.

**Dosyalar:** `storage.py` (yeni kolon), `services/feedback_service.py`, `app.py`

### 2.2 Session-içi bellek bozunması (intra-session decay)

**Sorun:** Session memory'de 2 saat önceki reddetme ile 2 dakika önceki reddetme aynı ağırlıkta.

**Çözüm:**
```python
def session_penalty(aged_minutes: float, base_salience: float) -> float:
    return base_salience * (0.5 ** (aged_minutes / 30.0))  # 30-dakika yarı ömür
```

**Etki:** Eski session kararlarının etkisi doğal olarak azalır; taze kararlar baskın kalır.

**Dosyalar:** `services/session_service.py` (veya ilgili session penalty kodu)

### 2.3 Çapraz-session tercih öğrenme

**Sorun:** Session A'da reddedilen pattern, Session B'de yeniden önerilir. Kullanıcı her seferinde reddeder.

**Çözüm:**
- `user_rejection_stats` tablosu: `(user_scope, pattern_id, variant_id, rejection_count, last_rejected_at)`
- 3+ kez reddedilen pattern → `preference_rules` tablosuna otomatik negatif kural ekle
- `issue_list_preferences`'da "auto-learned" flag'iyle göster

**Etki:** Sistem, kullanıcının tekrarlanan tercihlerini öğrenir; aynı hatayı tekrarlamaz.

**Dosyalar:** `storage.py` (yeni tablo), `services/feedback_service.py`, `services/preference_service.py`

### 2.4 Match yanıtında karar açıklamaları

**Sorun:** Kullanıcı neden bu öneriyi aldığını (veya almadığını) bilmiyor.

**Çözüm:**
- `issue_match` yanıtına `"reasoning"` alanı ekle:
  ```json
  {
    "reasoning": {
      "top_signals": ["root_cause_class match (0.18)", "feedback history (+0.12)"],
      "session_memory": "30 dk önce reddedildi, penalty uygulandı",
      "strategy_bandit": "shadow modda gözlem kaydedildi"
    }
  }
  ```

**Etki:** Şeffaflık; kullanıcı sistemin kararlarını anlayabilir ve daha iyi feedback verebilir.

**Dosyalar:** `retrieval/ranker.py`, `app.py`, `models.py`

---

## Faz 3 — Gelişmiş Öğrenme Altyapısı  *(uzun vadeli)*

### 3.1 Multi-faktör posterior'lar

**Sorun:** Mevcut posterior tek boyutlu: `(success, trials)`. Başarı oranı yüksek ama FP oranı da yüksek bir strateji aynı görünüyor.

**Çözüm:**
Strateji başına 3 bağımsız posterior:
- `quality_posterior`: `(fix_verified_count, total_suggestions)` → çözüm kalitesi
- `safety_posterior`: `(non_fp_count, total_suggestions)` → FP oranı  
- `adoption_posterior`: `(accepted_count, exposed_repos)` → kullanım yaygınlığı

Nihai strateji skoru: `quality × safety × adoption^0.5`

**Dosyalar:** `learning/posteriors.py`, `learning/strategy_bandit.py`, `storage.py`

### 3.2 Strateji ailesi hiyerarşisi

**Sorun:** Bireysel stratejiler bağımsız öğreniyor. `install_missing_dependency` ve `fix_version_conflict` aslında aynı ailenin (bağımlılık yönetimi) üyeleri.

**Çözüm:**
- `strategy_families` tablosu: ör. `{dependency_management: [install_missing_dependency, fix_version_conflict, update_lockfile]}`
- Aile seviyesinde paylaşımlı prior: düşük kanıtlı stratejiler aile ortalamasından faydalanır
- Hierarchical Bayesian: `family_prior → strategy_obs → strategy_posterior`

**Dosyalar:** `learning/strategy_bandit.py`, `normalization/class_hints.py`, `storage.py`

### 3.3 A/B test çerçevesi

**Sorun:** Yeni ranking ağırlıkları veya eşik değişikliklerini güvenli şekilde doğrulamanın yolu yok.

**Çözüm:**
- `experiment_registry` tablosu: `(experiment_id, treatment, control, start_date, end_date, status)`
- `issue_match` çağrısında experiment assignment (consistent hash on session_id)
- Treatment grubuna farklı ağırlık/eşik uygula, control grubuna mevcut
- `maintenance.py`'de `analyze-experiment` komutu: treatment vs control istatistikleri

**Dosyalar:** `storage.py`, `app.py`, `retrieval/ranker.py`, `maintenance.py`

### 3.4 Otomatik ağırlık kalibrasyonu

**Sorun:** 23 ranking ağırlığı deneysel; hiç kalibrasyon yok.

**Çözüm:**
- Faz 0.3'teki `feature_outcome_log` verisini kullanarak hata ailesi başına optimal ağırlıkları hesapla
- Basit logistic regression veya gradient-free optimizasyon:
  - Hedef: accepted/verified feedback'lerin sıralamasını maximize et
  - Kısıt: ağırlık değişimi ±0.05 adım ile sınırlı (kararlılık)
- Sonucu `calibration_profile.json`'a yaz; ranker başlatılırken yükle

**Dosyalar:** `maintenance.py`, `retrieval/ranker.py`, kalibrasyon profili

---

## Faz 4 — Retrieval Kalitesi  *(sürekli iyileştirme)*

### 4.1 IDF-bazlı token önceliklendirme

**Sorun:** FTS sorgusunda tüm tokenlar eşit ağırlıkta. `error`, `the`, `in` gibi sık kelimeler gerçek diagnostik tokenları (`OutOfMemoryError`, `CUDA`) bastırıyor.

**Çözüm:**
- `token_idf` tablosu: her token için doküman frekansı (pattern sayısı)
- FTS sorgusu oluştururken tokenları IDF'e göre sırala; en yüksek IDF'li 20 token'ı kullan
- Düşük IDF'li tokenları (`error`, `failed`, `the`) FTS'den çıkar

**Dosyalar:** `retrieval/candidate_retriever.py`, `storage.py`

### 4.2 Eş anlamlı genişletme

**Sorun:** `missing dependency` vs `import error` vs `module not found` — semantik olarak aynı hatalar, farklı n-gram'lar.

**Çözüm:**
- `normalization/synonyms.py`: 50-100 teknik eş anlamlı çift: `{("missing", "not found"), ("import", "module"), ...}`
- Tokenizasyon sırasında eş anlamlı genişletme: orijinal + eş anlamlı tokenlar birlikte hash'lenir
- Dense embedding'de eş anlamlı vektörlerin ortalaması alınır

**Dosyalar:** `normalization/` (yeni modül), `retrieval/dense_index.py`

### 4.3 Entity slot öğrenme

**Sorun:** Entity conflict penalty'si statik (-0.18). Ancak bazı entity çakışmaları önemsiz (farklı config sürümleri), bazıları kritik (farklı dtype).

**Çözüm:**
- Feedback verisinden entity conflict → outcome korelasyonunu izle
- `entity_importance` tablosu: `(entity_key, error_family, importance_weight)`
- Başlangıç değerleri sabit; feedback ile güncelleme

**Dosyalar:** `retrieval/features.py`, `storage.py`

---

## Faz 5 — Operasyonel Olgunluk  *(devam eden)*

### 5.1 Strateji bazında metrikler

- `issue_metrics()` yanıtına strateji bazında istatistikler ekle:
  ```json
  {
    "strategy_metrics": {
      "install_missing_dependency": {
        "suggestions": 42, "accepted": 31, "fp_rate": 0.05,
        "mean_reward": 0.72
      }
    }
  }
  ```

### 5.2 Gecikme (latency) dokümanı

- Her pipeline aşamasında zamanlama: retrieval, ranking, bandit, decision
- `issue_metrics`'e latency breakdown ekle

### 5.3 Degradasyon alarmı

- FP oranı %30+ arttığında `issue_metrics` uyarı flag'i döndürsün
- `doctor` komutu FP trend analizi yapsın

### 5.4 Batch learning güvenlik kapısı

- Anlık feedback güncelleme yerine 5-dakikalık batch window
- Aynı pattern için 5+ FP gelirse otomatik review queue'ye al
- Tek bir feedback'in posterior'ı aşırı kaydırmasını önle

---

## Önceliklendirme Matrisi

| Faz | Öğe | Etki | Efor | Dosya Sayısı | Bağımlılık |
|-----|-----|------|------|:---:|------------|
| 0.1 | Feedback döngüsü tamamlama | **Çok Yüksek** | Düşük | 2 | — |
| 0.2 | Proven score özelliği | **Yüksek** | Düşük | 2 | 0.1 |
| 0.3 | Feature-outcome log | **Yüksek** | Orta | 3 | — |
| 1.1 | Ailesi bazında eşik | **Yüksek** | Orta | 3 | 0.3 |
| 1.2 | Bağlamsal yarı-ömür | **Orta** | Orta | 2 | — |
| 1.3 | Aile bazında ağırlık | **Yüksek** | Orta | 2 | 0.3, 1.1 |
| 1.4 | FP asimetrik maliyet | **Yüksek** | Düşük | 2 | — |
| 2.1 | Örtük reddetme | **Yüksek** | Orta | 3 | 0.1 |
| 2.2 | Session bozunma | **Orta** | Düşük | 1 | — |
| 2.3 | Çapraz-session tercih | **Yüksek** | Orta | 3 | 0.1 |
| 2.4 | Karar açıklamaları | **Orta** | Orta | 3 | — |
| 3.1 | Multi-faktör posterior | **Çok Yüksek** | Yüksek | 3 | 0.3, 1.4 |
| 3.2 | Strateji ailesi | **Yüksek** | Yüksek | 3 | 3.1 |
| 3.3 | A/B test çerçevesi | **Yüksek** | Yüksek | 4 | — |
| 3.4 | Otomatik kalibrasyon | **Çok Yüksek** | Yüksek | 2 | 0.3, 3.3 |
| 4.1 | IDF token öncelik | **Orta** | Orta | 2 | — |
| 4.2 | Eş anlamlı genişletme | **Orta** | Orta | 2 | — |
| 4.3 | Entity slot öğrenme | **Orta** | Orta | 2 | 0.3 |
| 5.1 | Strateji metrikleri | **Orta** | Düşük | 1 | — |
| 5.2 | Latency izleme | **Düşük** | Düşük | 2 | — |
| 5.3 | Degradasyon alarmı | **Orta** | Düşük | 1 | 5.1 |
| 5.4 | Batch learning kapısı | **Yüksek** | Orta | 2 | 0.1 |

---

## Mevcut Durum → Hedef Durum

```
Mevcut (v0.1.0):

  Sinyal Toplama  ████████████████████ 90%  ← harika temeller
  Sinyal Kullanma ████░░░░░░░░░░░░░░░░ 25%  ← ana zayıflık
  Bağlam Duyarlık █████░░░░░░░░░░░░░░░ 30%  ← tek tip eşikler
  Kapalı Döngüler ████░░░░░░░░░░░░░░░░ 20%  ← kırık döngüler
  Şeffaflık       ████████░░░░░░░░░░░░ 45%  ← metrics var, reasoning yok

Hedef (v0.3.0):

  Sinyal Toplama  █████████████████████ 95%
  Sinyal Kullanma ████████████████░░░░ 80%
  Bağlam Duyarlık ████████████████░░░░ 80%
  Kapalı Döngüler █████████████████░░░ 85%
  Şeffaflık       ████████████████████ 90%
```

---

## Versiyon Hedefleri

### v0.2.0 — "Learning Loops" *(Faz 0 + Faz 1)*
- Tüm feedback tiplerinin variant stat'larına bağlanması
- `proven_score` ranking özelliği
- Feature-outcome korelasyon altyapısı
- Hata ailesi bazında eşik kalibrasyonu
- FP asimetrik ağırlıklandırma
- Bağlamsal yarı-ömür

### v0.2.5 — "Silent Signals" *(Faz 2)*
- Örtük reddetme tespiti
- Session-içi bellek bozunması
- Çapraz-session tercih öğrenme
- Match yanıtında karar açıklamaları

### v0.3.0 — "Adaptive Intelligence" *(Faz 3 + Faz 4)*
- Multi-faktör posterior'lar
- Strateji ailesi hiyerarşisi
- A/B test çerçevesi
- Otomatik ağırlık kalibrasyonu
- IDF-bazlı token önceliklendirme
- Eş anlamlı genişletme
