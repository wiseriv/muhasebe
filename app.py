import streamlit as st
import os
import pandas as pd
from PIL import Image
import io
import json
import requests
import base64
import concurrent.futures # Paralel işlem kütüphanesi
import time

# --- AYARLAR ---
st.set_page_config(page_title="Mihsap AI - Turbo", layout="wide", page_icon="🚀")
API_KEY = st.secrets.get("GEMINI_API_KEY")

if not API_KEY:
    st.error("Lütfen Secrets ayarlarından GEMINI_API_KEY'i ekleyin.")
    st.stop()

# --- OPTİMİZASYON FONKSİYONU ---
def resmi_hazirla(image_bytes):
    """
    Resmi küçültür ve sıkıştırır (Hızın Sırrı Buradadır).
    Büyük resim göndermek zaman kaybıdır.
    """
    img = Image.open(io.BytesIO(image_bytes))
    
    # Eğer resim PNG ise JPEG yap (Daha az yer kaplar)
    if img.mode in ("RGBA", "P"): 
        img = img.convert("RGB")
    
    # Boyutlandırma: En uzun kenarı 1024 piksel yap (Okunabilirlik bozulmaz)
    img.thumbnail((1024, 1024))
    
    # Sıkıştırılmış çıktı al
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85) # %85 kalite yeterli
    return base64.b64encode(buf.getvalue()).decode('utf-8')

def gemini_ile_analiz_et(dosya_objesi, secilen_model):
    """Tek bir dosyayı analiz eden fonksiyon."""
    try:
        # Dosya ismini al
        dosya_adi = dosya_objesi.name
        
        # Resmi Hızlıca Hazırla (Sıkıştır)
        base64_image = resmi_hazirla(dosya_objesi.getvalue())
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{secilen_model}:generateContent?key={API_KEY}"
        headers = {'Content-Type': 'application/json'}
        
        payload = {
            "contents": [{
                "parts": [
                    {"text": """Bu fiş görüntüsünü analiz et. 
                    Cevabı SADECE aşağıdaki formatta saf JSON olarak ver:
                    {
                        "isyeri_adi": "İşyeri Adı",
                        "fiş_no": "Belge No",
                        "tarih": "GG.AA.YYYY",
                        "toplam_tutar": "00.00",
                        "toplam_kdv": "00.00"
                    }"""},
                    {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}
                ]
            }]
        }

        # İsteği Gönder
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code == 429:
            return {"dosya_adi": dosya_adi, "hata": "Hız Sınırı (Kota) Aşıldı! Biraz bekle."}
            
        if response.status_code != 200:
            return {"dosya_adi": dosya_adi, "hata": f"Google Hatası: {response.status_code}"}

        sonuc_json = response.json()
        metin = sonuc_json['candidates'][0]['content']['parts'][0]['text']
        metin = metin.replace("```json", "").replace("```", "").strip()
        
        veri = json.loads(metin)
        veri["dosya_adi"] = dosya_adi # Dosya adını ekle
        return veri

    except Exception as e:
        return {"dosya_adi": dosya_adi, "hata": str(e)}

# --- ARAYÜZ ---
with st.sidebar:
    st.header("🚀 Turbo Ayarları")
    # Flash modeli en hızlısıdır
    secilen_model = st.selectbox("Model", ["gemini-1.5-flash", "gemini-1.5-flash-latest"], index=0)
    
    # İşçi Sayısı (Worker): Aynı anda kaç fiş gitsin?
    # Ücretsiz planda 15 RPM sınırı var. Çok artırırsan 429 alırsın.
    isci_sayisi = st.slider("Eşzamanlı İşlem Sayısı", min_value=1, max_value=5, value=3)
    st.caption("Not: Sayıyı artırmak hızı artırır ama 'Kota Hatası' riskini yükseltir.")

st.title("🚀 Mihsap AI (Turbo Mod)")
st.write("Resim sıkıştırma ve paralel işleme ile maksimum hız.")

yuklenen_dosyalar = st.file_uploader("Fişleri Yükle (50-100 tane deneyebilirsin)", type=['jpg', 'png', 'jpeg'], accept_multiple_files=True)

if yuklenen_dosyalar:
    if st.button("🔥 Analizi Başlat"):
        
        tum_veriler = []
        hatali_dosyalar = []
        
        bar = st.progress(0)
        durum = st.empty()
        
        # --- PARALEL İŞLEME MOTORU ---
        # ThreadPoolExecutor: Aynı anda birden fazla işçi çalıştırır
        with concurrent.futures.ThreadPoolExecutor(max_workers=isci_sayisi) as executor:
            
            # Görevleri dağıt
            future_to_file = {executor.submit(gemini_ile_analiz_et, dosya, secilen_model): dosya for dosya in yuklenen_dosyalar}
            
            tamamlanan = 0
            for future in concurrent.futures.as_completed(future_to_file):
                sonuc = future.result()
                
                if "hata" in sonuc:
                    hatali_dosyalar.append(sonuc)
                else:
                    tum_veriler.append(sonuc)
                
                tamamlanan += 1
                bar.progress(tamamlanan / len(yuklenen_dosyalar))
                durum.text(f"Tamamlanan: {tamamlanan} / {len(yuklenen_dosyalar)}")
                
                # Ücretsiz planı patlatmamak için minik bir fren
                time.sleep(0.5) 

        # --- SONUÇLARI GÖSTER ---
        st.success("İşlem Bitti!")
        
        if tum_veriler:
            df = pd.DataFrame(tum_veriler)
            st.write("### ✅ Başarılı İşlemler")
            # Sütun sırası
            cols = ["dosya_adi", "isyeri_adi", "fiş_no", "tarih", "toplam_tutar", "toplam_kdv"]
            mevcut = [c for c in cols if c in df.columns]
            st.dataframe(df[mevcut], use_container_width=True)
            
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            st.download_button("📥 Excel İndir", data=buffer.getvalue(), file_name="turbo_muhasebe.xlsx", type="primary")
        
        if hatali_dosyalar:
            st.error(f"{len(hatali_dosyalar)} adet dosyada hata oluştu.")
            st.dataframe(pd.DataFrame(hatali_dosyalar))
