import streamlit as st
import os
import re
import pandas as pd
from google.cloud import vision
from PIL import Image, ImageOps
import io
import json

# --- AYARLAR ---
if os.path.exists('google_key.json'):
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = 'google_key.json'
else:
    if "gcp_service_account" in st.secrets:
        key_dict = dict(st.secrets["gcp_service_account"])
        with open("google_key.json", "w") as f:
            json.dump(key_dict, f)
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = 'google_key.json'

def google_vision_ile_oku(image_bytes):
    """Görüntüyü Google'a gönderir."""
    try:
        client = vision.ImageAnnotatorClient()
        image = vision.Image(content=image_bytes)
        response = client.text_detection(image=image)
        texts = response.text_annotations
        if texts:
            return texts[0].description
        return None
    except Exception:
        return None

def veriyi_anlamlandir(ham_metin, dosya_adi):
    """Metinden verileri çeker."""
    veri = {
        "Dosya Adı": dosya_adi,
        "Isyeri": "Bulunamadı",
        "Tarih": "Bulunamadı",
        "Toplam_Tutar": "0.00",
        "Toplam_KDV": "0.00",
        "Basari_Puani": 0 # Veri buldukça artacak
    }
    
    if not ham_metin: return veri
    
    satirlar = ham_metin.split('\n')
    if len(satirlar) > 0: veri["Isyeri"] = satirlar[0]

    # Tarih Bulma
    tarih_match = re.search(r'(\d{2}[./-]\d{2}[./-]\d{4})', ham_metin)
    if tarih_match: 
        veri["Tarih"] = tarih_match.group(1)
        veri["Basari_Puani"] += 1 # Tarih bulduysak +1 puan

    for i in range(len(satirlar)):
        satir = satirlar[i]
        satir_kucuk = satir.lower()
        
        def para_bul(metin):
            rakamlar = re.findall(r'[*T₺]?\s*(\d+[.,]\d{2})', metin)
            if rakamlar: return rakamlar[-1].replace('*', '').replace('T', '').replace('₺', '')
            return None

        # TOPLAM TUTAR
        if ("toplam" in satir_kucuk or "top" in satir_kucuk) and "kdv" not in satir_kucuk:
            bulunan = para_bul(satir)
            if bulunan: 
                veri["Toplam_Tutar"] = bulunan
                veri["Basari_Puani"] += 2 # Tutar bulmak çok önemli +2 puan
            elif i + 1 < len(satirlar):
                bulunan_alt = para_bul(satirlar[i+1])
                if bulunan_alt: 
                    veri["Toplam_Tutar"] = bulunan_alt
                    veri["Basari_Puani"] += 2

        # KDV
        if "topkdv" in satir_kucuk or ("toplam" in satir_kucuk and "kdv" in satir_kucuk):
             bulunan_kdv = para_bul(satir)
             if bulunan_kdv: veri["Toplam_KDV"] = bulunan_kdv
             elif i + 1 < len(satirlar):
                bulunan_alt_kdv = para_bul(satirlar[i+1])
                if bulunan_alt_kdv: veri["Toplam_KDV"] = bulunan_alt_kdv
                
    return veri

# --- WEB ARAYÜZÜ ---
st.set_page_config(page_title="Mihsap Pro - Otopilot", layout="wide", page_icon="🤖")

st.title("🤖 Otopilot Fiş Okuyucu")
st.write("Fişleri karışık (yan/düz) yükleyin, yapay zeka yönünü kendisi bulsun.")

yuklenen_dosyalar = st.file_uploader("Fişleri Yükle (Toplu Seçim Yapabilirsin)", type=['jpg', 'png', 'jpeg'], accept_multiple_files=True)

if yuklenen_dosyalar:
    st.info(f"{len(yuklenen_dosyalar)} adet dosya kuyruğa alındı. İşleniyor...")
    tum_veriler = []
    progress_bar = st.progress(0)
    durum_kutusu = st.empty()
    
    for i, dosya in enumerate(yuklenen_dosyalar):
        durum_kutusu.text(f"İnceleniyor: {dosya.name}")
        
        # 1. Resmi Aç
        orijinal_resim = Image.open(dosya)
        # EXIF bilgisini düzelt (Telefonun kendi döndürmesi)
        orijinal_resim = ImageOps.exif_transpose(orijinal_resim)
        
        # --- AKILLI DÖNGÜ (AUTO-RETRY LOOP) ---
        en_iyi_veri = None
        en_yuksek_puan = -1
        
        # Denenecek açılar: 0 (Orijinal) ve 270 (Sola yatık - en sık karşılaşılan durum)
        # İstersen listeye 90 ve 180 de eklenebilir ama maliyeti artırır.
        acilar = [0, 270] 
        
        for aci in acilar:
            # Resmi bellekte döndür
            if aci == 0:
                islenen_resim = orijinal_resim
            else:
                islenen_resim = orijinal_resim.rotate(aci, expand=True)
            
            # Byte'a çevir
            img_byte_arr = io.BytesIO()
            islenen_resim.save(img_byte_arr, format='JPEG') # JPEG daha hızlı
            bytes_data = img_byte_arr.getvalue()
            
            # Google'a sor
            metin = google_vision_ile_oku(bytes_data)
            
            if metin:
                analiz = veriyi_anlamlandir(metin, dosya.name)
                
                # Eğer bu denemenin puanı daha yüksekse, bunu "en iyi sonuç" olarak kaydet
                if analiz["Basari_Puani"] > en_yuksek_puan:
                    en_yuksek_puan = analiz["Basari_Puani"]
                    en_iyi_veri = analiz
                
                # Eğer Tarih ve Tutar bulduysak (Puan >= 3) diğer açıları denemene gerek yok, döngüyü kır!
                if en_yuksek_puan >= 3:
                    break
        
        # En iyi sonucu listeye ekle
        if en_iyi_veri:
            tum_veriler.append(en_iyi_veri)
        
        progress_bar.progress((i + 1) / len(yuklenen_dosyalar))
    
    durum_kutusu.success("✅ Bitti!")
    
    if tum_veriler:
        df = pd.DataFrame(tum_veriler)
        
        # Tabloda başarı puanını göstermeye gerek yok, kaldıralım
        if "Basari_Puani" in df.columns:
            df = df.drop(columns=["Basari_Puani"])
            
        st.write("### 📊 Sonuçlar")
        st.dataframe(df, use_container_width=True)
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
            
        st.download_button("📥 Excel İndir", data=buffer.getvalue(), file_name="otopilot_muhasebe.xlsx", type="primary")
