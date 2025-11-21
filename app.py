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
        "Toplam_KDV": "0.00"
    }
    
    if not ham_metin: return veri
    
    satirlar = ham_metin.split('\n')
    if len(satirlar) > 0: veri["Isyeri"] = satirlar[0]

    # Tarih
    tarih_match = re.search(r'(\d{2}[./-]\d{2}[./-]\d{4})', ham_metin)
    if tarih_match: veri["Tarih"] = tarih_match.group(1)

    # Rakam Temizleyici
    def rakam_al(metin):
        # Tüm harfleri temizle, sadece rakam ve virgül/nokta bırak
        bulunanlar = re.findall(r'(\d+[.,]\d{2})', metin)
        temiz_rakamlar = []
        for b in bulunanlar:
            # Virgülü noktaya çevirip float yapalım ki kıyaslayabilelim
            try:
                deger = float(b.replace(',', '.'))
                temiz_rakamlar.append(deger)
            except:
                pass
        return temiz_rakamlar

    # --- ADAY LİSTELERİ (HAVUZ MANTIĞI) ---
    toplam_adaylari = [] # Bulduğumuz tüm olası toplamları buraya atacağız
    kdv_adaylari = []    # Bulduğumuz tüm olası KDV'leri buraya atacağız

    for i in range(len(satirlar)):
        satir = satirlar[i]
        satir_kucuk = satir.lower()

        # 1. TOPLAM ADAYLARINI TOPLA
        # "Toplam" veya "Top" geçen her yerdeki rakamı al
        if ("toplam" in satir_kucuk or "top" in satir_kucuk):
            
            # A) Bu satırdaki rakamlar
            rakamlar = rakam_al(satir)
            toplam_adaylari.extend(rakamlar)
            
            # B) Bir alt satırdaki rakamlar (Doğan Büfe gibi durumlar için)
            if i + 1 < len(satirlar):
                rakamlar_alt = rakam_al(satirlar[i+1])
                toplam_adaylari.extend(rakamlar_alt)

        # 2. KDV ADAYLARINI TOPLA
        if "kdv" in satir_kucuk:
             rakamlar_kdv = rakam_al(satir)
             kdv_adaylari.extend(rakamlar_kdv)
             
             if i + 1 < len(satirlar):
                 rakamlar_alt_kdv = rakam_al(satirlar[i+1])
                 kdv_adaylari.extend(rakamlar_alt_kdv)

    # --- KARAR MEKANİZMASI ---
    
    # Toplam Tutar: Adaylar içindeki EN BÜYÜK rakam (Matematiksel Kesinlik)
    if toplam_adaylari:
        en_buyuk_tutar = max(toplam_adaylari)
        veri["Toplam_Tutar"] = f"{en_buyuk_tutar:.2f}"
        
        # KDV Mantığı: Eğer KDV adayımız varsa onu al
        # Eğer KDV adayımız Toplam Tutara eşitse (Hata varsa), ikinci en büyüğü al
        if kdv_adaylari:
            en_buyuk_kdv = max(kdv_adaylari)
            
            # Eğer bulduğumuz KDV, Toplam Tutar ile aynıysa (yanlışlıkla aynı satırı okuduysa)
            if en_buyuk_kdv == en_buyuk_tutar and len(kdv_adaylari) > 1:
                # Listeden en büyüğü çıkar, kalanların en büyüğünü al
                kdv_adaylari.remove(en_buyuk_kdv)
                en_buyuk_kdv = max(kdv_adaylari)
            
            # KDV asla Toplamdan büyük olamaz, eğer öyleyse KDV 0'dır veya hatadır
            if en_buyuk_kdv < en_buyuk_tutar:
                veri["Toplam_KDV"] = f"{en_buyuk_kdv:.2f}"

    return veri

# --- WEB ARAYÜZÜ ---
st.set_page_config(page_title="Mihsap Pro - Akıllı Analiz", layout="wide", page_icon="🧠")
st.title("🧠 Akıllı Fiş Analizi (V5)")
st.info("Matematiksel doğrulama modu devrede. Fişteki en büyük rakam Toplam kabul edilir.")

yuklenen_dosyalar = st.file_uploader("Fişleri Yükle", type=['jpg', 'png', 'jpeg'], accept_multiple_files=True)

if yuklenen_dosyalar:
    tum_veriler = []
    progress_bar = st.progress(0)
    
    for i, dosya in enumerate(yuklenen_dosyalar):
        # Görüntü işleme
        img = Image.open(dosya)
        img = ImageOps.exif_transpose(img)
        
        # Otopilot (Açı Deneme)
        en_iyi_veri = None
        max_toplam = -1.0
        
        # 0 ve 270 derece dene (En sık karşılaşılanlar)
        for aci in [0, 270, 90]:
            if aci == 0: work_img = img
            else: work_img = img.rotate(aci, expand=True)
            
            buf = io.BytesIO()
            work_img.save(buf, format='JPEG')
            
            metin = google_vision_ile_oku(buf.getvalue())
            
            if metin:
                analiz = veriyi_anlamlandir(metin, dosya.name)
                
                # Hangi açı daha büyük bir "Toplam Tutar" bulduysa onu doğru kabul et
                # Çünkü yanlış okumalarda genelde rakam bulamaz veya küçük parçalar bulur.
                try:
                    bulunan_tutar = float(analiz["Toplam_Tutar"])
                except:
                    bulunan_tutar = 0
                
                if bulunan_tutar > max_toplam:
                    max_toplam = bulunan_tutar
                    en_iyi_veri = analiz

        if en_iyi_veri:
            tum_veriler.append(en_iyi_veri)
        
        progress_bar.progress((i + 1) / len(yuklenen_dosyalar))
    
    if tum_veriler:
        df = pd.DataFrame(tum_veriler)
        st.write("### 📊 Sonuçlar")
        st.dataframe(df, use_container_width=True)
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        st.download_button("📥 Excel İndir", data=buffer.getvalue(), file_name="muhasebe_smart.xlsx", type="primary")
