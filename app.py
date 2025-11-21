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
    veri = {
        "Dosya Adı": dosya_adi,
        "Isyeri": "Bulunamadı",
        "Tarih": "Bulunamadı",
        "Toplam_Tutar": "0.00",
        "Toplam_KDV": "0.00",
        "Basari_Puani": 0,
        "Okunan_Metin_Ozeti": ham_metin[:100].replace('\n', ' ') # Debug için
    }
    
    if not ham_metin: return veri
    
    satirlar = ham_metin.split('\n')
    if len(satirlar) > 0: veri["Isyeri"] = satirlar[0]

    # Tarih
    tarih_match = re.search(r'(\d{2}[./-]\d{2}[./-]\d{4})', ham_metin)
    if tarih_match: 
        veri["Tarih"] = tarih_match.group(1)
        veri["Basari_Puani"] += 1

    # --- GELİŞMİŞ FİYAT ARAMA FONKSİYONU ---
    def para_bul(metin):
        # * : T TL harflerini ve boşlukları temizler, rakamı çeker
        rakamlar = re.findall(r'[*T₺:.]?\s*(\d+[.,]\d{2})', metin)
        if rakamlar: 
            tutar = rakamlar[-1]
            # Temizlik
            tutar = tutar.replace('*', '').replace('T', '').replace('₺', '').replace(':', '')
            return tutar
        return None

    for i in range(len(satirlar)):
        satir = satirlar[i]
        satir_kucuk = satir.lower()

        # TOPLAM TUTAR (DERİN ARAMA MODU)
        if ("toplam" in satir_kucuk or "top" in satir_kucuk) and "kdv" not in satir_kucuk:
            # 1. Aynı satıra bak
            bulunan = para_bul(satir)
            if bulunan: 
                veri["Toplam_Tutar"] = bulunan
                veri["Basari_Puani"] += 2
            
            # 2. Eğer yoksa, ALTTAKİ 3 SATIRA KADAR BAK (Migros/Doğan Büfe fix)
            else:
                for j in range(1, 4): # i+1, i+2, i+3
                    if i + j < len(satirlar):
                        alt_satir = satirlar[i+j]
                        bulunan_alt = para_bul(alt_satir)
                        if bulunan_alt: 
                            veri["Toplam_Tutar"] = bulunan_alt
                            veri["Basari_Puani"] += 2
                            break # Bulunca döngüden çık

        # KDV (DERİN ARAMA MODU)
        if "topkdv" in satir_kucuk or ("toplam" in satir_kucuk and "kdv" in satir_kucuk):
             bulunan_kdv = para_bul(satir)
             if bulunan_kdv: veri["Toplam_KDV"] = bulunan_kdv
             else:
                for j in range(1, 4):
                    if i + j < len(satirlar):
                        alt_satir = satirlar[i+j]
                        bulunan_alt_kdv = para_bul(alt_satir)
                        if bulunan_alt_kdv: 
                            veri["Toplam_KDV"] = bulunan_alt_kdv
                            break

    return veri

# --- WEB ARAYÜZÜ ---
st.set_page_config(page_title="Mihsap Pro - V3", layout="wide", page_icon="🔥")

st.title("🔥 Otopilot V3 (Derin Arama)")
st.write("Tüm açıları dener, satır atlamalarını yakalar.")

yuklenen_dosyalar = st.file_uploader("Fişleri Yükle", type=['jpg', 'png', 'jpeg'], accept_multiple_files=True)

if yuklenen_dosyalar:
    st.info(f"{len(yuklenen_dosyalar)} dosya işleniyor...")
    tum_veriler = []
    progress_bar = st.progress(0)
    
    for i, dosya in enumerate(yuklenen_dosyalar):
        orijinal_resim = Image.open(dosya)
        orijinal_resim = ImageOps.exif_transpose(orijinal_resim)
        
        en_iyi_veri = None
        en_yuksek_puan = -1
        
        # YENİLİK: 90 dereceyi de ekledik!
        acilar = [0, 270, 90] 
        
        for aci in acilar:
            if aci == 0: islenen_resim = orijinal_resim
            else: islenen_resim = orijinal_resim.rotate(aci, expand=True)
            
            img_byte_arr = io.BytesIO()
            islenen_resim.save(img_byte_arr, format='JPEG')
            bytes_data = img_byte_arr.getvalue()
            
            metin = google_vision_ile_oku(bytes_data)
            
            if metin:
                analiz = veriyi_anlamlandir(metin, dosya.name)
                
                # Puanlama sistemi en iyi sonucu seçer
                if analiz["Basari_Puani"] > en_yuksek_puan:
                    en_yuksek_puan = analiz["Basari_Puani"]
                    en_iyi_veri = analiz
                
                # Mükemmel sonuç (Tarih + Tutar) bulduysa dur
                if en_yuksek_puan >= 3:
                    break
        
        if en_iyi_veri:
            tum_veriler.append(en_iyi_veri)
        
        progress_bar.progress((i + 1) / len(yuklenen_dosyalar))
    
    if tum_veriler:
        df = pd.DataFrame(tum_veriler)
        
        # Analiz için puan sütununu gizlemiyoruz, görelim diye
        st.write("### 📊 Sonuçlar")
        st.dataframe(df, use_container_width=True)
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
            
        st.download_button("📥 Excel İndir", data=buffer.getvalue(), file_name="muhasebe_v3.xlsx", type="primary")
