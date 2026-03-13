"""
Meydan Elektrik - Saha Ekibi Mobil Uygulaması
Ayrı Railway servisi olarak deploy edilir.
"""
import os
from dotenv import load_dotenv
load_dotenv()

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from datetime import datetime
from functools import wraps
from werkzeug.security import check_password_hash
import pymysql.cursors
from urllib.parse import urlparse

app = Flask(__name__, template_folder='saha_templates')
app.secret_key = os.getenv('SECRET_KEY', 'saha_meydan_2025_secret')
app.config['SESSION_PERMANENT'] = False

@app.before_request
def before_first():
    if not getattr(app, '_migrated', False):
        run_migrations()
        app._migrated = True  # Tarayıcı kapanınca oturum sona ersin

# ========== DB ==========
def get_db():
    mysql_url = os.getenv('MYSQL_PUBLIC_URL')
    if not mysql_url:
        raise Exception("MYSQL_PUBLIC_URL bulunamadı")
    parsed = urlparse(mysql_url)
    return pymysql.connect(
        host=parsed.hostname,
        port=parsed.port or 3306,
        user=parsed.username,
        password=parsed.password,
        database=parsed.path[1:],
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True
    )

# ========== DB MİGRASYON ==========
def run_migrations():
    """Saha app için gerekli DB değişikliklerini otomatik uygula"""
    try:
        db = get_db()
        cursor = db.cursor()
        # malzeme_id NULL olabilsin (manuel malzeme için)
        try:
            cursor.execute("ALTER TABLE servis_malzemeleri MODIFY COLUMN malzeme_id INT NULL")
        except: pass
        # malzeme_adi kolonu ekle (manuel malzeme adı)
        try:
            cursor.execute("ALTER TABLE servis_malzemeleri ADD COLUMN malzeme_adi VARCHAR(255) NULL AFTER malzeme_id")
        except: pass
        # birim kolonu ekle
        try:
            cursor.execute("ALTER TABLE servis_malzemeleri ADD COLUMN birim VARCHAR(50) DEFAULT 'adet' AFTER malzeme_adi")
        except: pass
        db.close()
        print("✅ DB migrasyon tamamlandı")
    except Exception as e:
        print(f"⚠️ Migrasyon uyarısı: {e}")

# ========== AUTH ==========
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'personel_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ========== ROUTES ==========

@app.route('/')
def index():
    if 'personel_id' in session:
        return redirect(url_for('anasayfa'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'personel_id' in session:
        return redirect(url_for('anasayfa'))
    
    error = None
    if request.method == 'POST':
        kullanici_adi = request.form.get('kullanici_adi', '').strip()
        sifre = request.form.get('sifre', '').strip()
        try:
            db = get_db()
            cursor = db.cursor()
            cursor.execute(
                "SELECT id, ad, soyad, kullanici_adi, sifre_hash, rol FROM personel WHERE kullanici_adi = %s AND aktif = 1",
                (kullanici_adi,)
            )
            personel = cursor.fetchone()
            db.close()

            if personel and personel['sifre_hash'] and check_password_hash(personel['sifre_hash'], sifre):
                session['personel_id'] = personel['id']
                session['personel_ad'] = f"{personel['ad']} {personel['soyad']}"
                session['kullanici_adi'] = personel['kullanici_adi']
                session['rol'] = personel['rol']
                return redirect(url_for('anasayfa'))
            else:
                error = 'Hatalı kullanıcı adı veya şifre!'
        except Exception as e:
            error = f'Bağlantı hatası: {str(e)}'
    
    return render_template('login.html', error=error)

@app.route('/cikis')
def cikis():
    session.clear()
    return redirect(url_for('login'))

@app.route('/anasayfa')
@login_required
def anasayfa():
    try:
        db = get_db()
        cursor = db.cursor()
        pid = session['personel_id']

        # Bugün servisler
        cursor.execute("""
            SELECT COUNT(*) as adet FROM servis_kayitlari
            WHERE personel_id = %s AND DATE(servis_tarihi) = CURDATE()
        """, (pid,))
        bugun = cursor.fetchone()['adet']

        # Toplam servisler
        cursor.execute("SELECT COUNT(*) as adet FROM servis_kayitlari WHERE personel_id = %s", (pid,))
        toplam = cursor.fetchone()['adet']

        # Son 5 servis
        cursor.execute("""
            SELECT s.id, s.servis_tarihi, s.servis_durumu, s.is_aciklamasi,
                   m.ad as musteri_ad, m.sirket_adi
            FROM servis_kayitlari s
            LEFT JOIN musteriler m ON s.musteri_id = m.id
            WHERE s.personel_id = %s
            ORDER BY s.kayit_tarihi DESC LIMIT 5
        """, (pid,))
        son_servisler = cursor.fetchall()

        # Bekleyen teklifler
        cursor.execute("""
            SELECT COUNT(*) as adet FROM teklifler
            WHERE personel_id = %s AND teklif_durumu = 'taslak'
        """, (pid,))
        taslak_teklif = cursor.fetchone()['adet']

        db.close()
    except Exception as e:
        print(f"Anasayfa hata: {e}")
        bugun = toplam = taslak_teklif = 0
        son_servisler = []

    return render_template('anasayfa.html',
        bugun=bugun, toplam=toplam,
        son_servisler=son_servisler,
        taslak_teklif=taslak_teklif
    )

@app.route('/musteri_ekle_hizli', methods=['POST'])
@login_required
def musteri_ekle_hizli():
    """Servis formundan hızlı müşteri ekleme"""
    try:
        ad = request.form.get('yeni_ad', '').strip()
        telefon = request.form.get('yeni_telefon', '').strip()
        sirket = request.form.get('yeni_sirket', '').strip()
        if not ad:
            return jsonify({'success': False, 'message': 'Ad zorunlu!'})
        db = get_db()
        cursor = db.cursor()
        cursor.execute("""
            INSERT INTO musteriler (ad, soyad, sirket_adi, telefon, musteri_tipi, aktif, kayit_tarihi)
            VALUES (%s, '', %s, %s, 'bireysel', 1, NOW())
        """, (ad, sirket or None, telefon or None))
        musteri_id = cursor.lastrowid
        db.close()
        label = sirket or ad
        if telefon:
            label += f' · {telefon}'
        return jsonify({'success': True, 'id': musteri_id, 'label': label, 'redirect': f'/yeni_servis?musteri_id={musteri_id}'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/yeni_servis', methods=['GET', 'POST'])
@login_required
def yeni_servis():
    if request.method == 'POST':
        try:
            db = get_db()
            cursor = db.cursor()

            musteri_id = request.form.get('musteri_id', '').strip()
            if not musteri_id:
                flash('❌ Lütfen müşteri seçin veya yeni müşteri ekleyin!', 'error')
                return redirect(url_for('yeni_servis'))
            musteri_id = int(musteri_id)
            tarih = request.form.get('servis_tarihi')
            saat = request.form.get('servis_saati', '09:00')
            aciklama = request.form.get('is_aciklamasi', '').strip()
            iscilik = float(request.form.get('iscilik_ucreti', 0) or 0)

            # Malzemeleri topla
            malzeme_adlari = request.form.getlist('malzeme_ad[]')
            miktarlar = request.form.getlist('malzeme_miktar[]')
            birimler = request.form.getlist('malzeme_birim[]')
            birim_fiyatlar = request.form.getlist('malzeme_birim_fiyat[]')

            malzeme_toplam = 0
            malzemeler_veri = []
            for i, ad in enumerate(malzeme_adlari):
                ad = ad.strip()
                if not ad:
                    continue
                miktar = float(miktarlar[i]) if i < len(miktarlar) else 1
                birim = birimler[i] if i < len(birimler) else 'adet'
                bf = float(birim_fiyatlar[i]) if i < len(birim_fiyatlar) else 0
                toplam = round(miktar * bf, 2)
                malzeme_toplam += toplam
                malzemeler_veri.append((ad, miktar, birim, bf, toplam))

            toplam_tutar = round(malzeme_toplam + iscilik, 2)

            cursor.execute("""
                INSERT INTO servis_kayitlari
                (musteri_id, personel_id, servis_tarihi, servis_saati,
                 is_aciklamasi, yapilacak_calisma, iscilik_ucreti, toplam_tutar,
                 servis_durumu, kaynak, kayit_tarihi)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'beklemede', 'saha', NOW())
            """, (musteri_id, session['personel_id'], tarih, saat,
                  aciklama, aciklama, iscilik, toplam_tutar))

            servis_id = cursor.lastrowid

            for (ad, miktar, birim, bf, top) in malzemeler_veri:
                try:
                    cursor.execute("""
                        INSERT INTO servis_malzemeleri
                        (servis_id, malzeme_id, malzeme_adi, miktar, birim, birim_fiyat)
                        VALUES (%s, NULL, %s, %s, %s, %s)
                    """, (servis_id, ad, miktar, birim, bf))
                except Exception as me:
                    print(f"Malzeme eklenemedi: {me}")

            db.close()
            flash('✅ Servis başarıyla kaydedildi!', 'success')
            return redirect(url_for('servislerim'))

        except Exception as e:
            flash(f'❌ Hata: {str(e)}', 'error')

    # GET - müşteri listesi
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT id, ad, soyad, sirket_adi, telefon FROM musteriler WHERE aktif=1 ORDER BY ad")
        musteriler = cursor.fetchall()
        db.close()
    except:
        musteriler = []

    today = datetime.now().strftime('%Y-%m-%d')
    # URL'den seçili müşteri (yeni müşteri eklendikten sonra)
    secili_musteri_id = request.args.get('musteri_id', '')
    return render_template('yeni_servis.html', musteriler=musteriler, today=today, secili_musteri_id=secili_musteri_id)

@app.route('/servislerim')
@login_required
def servislerim():
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute("""
            SELECT s.id, s.servis_tarihi, s.servis_saati, s.servis_durumu,
                   s.is_aciklamasi, s.kayit_tarihi,
                   m.ad as musteri_ad, m.soyad as musteri_soyad, m.sirket_adi,
                   m.telefon as musteri_tel
            FROM servis_kayitlari s
            LEFT JOIN musteriler m ON s.musteri_id = m.id
            WHERE s.personel_id = %s
            ORDER BY s.servis_tarihi DESC, s.servis_saati DESC
            LIMIT 50
        """, (session['personel_id'],))
        servisler = cursor.fetchall()
        db.close()
    except Exception as e:
        print(f"Servislerim hata: {e}")
        servisler = []

    return render_template('servislerim.html', servisler=servisler)

@app.route('/duzeltme_talebi/<int:servis_id>', methods=['POST'])
@login_required
def duzeltme_talebi(servis_id):
    try:
        talep = request.form.get('talep_aciklama', '').strip()
        if not talep:
            return jsonify({'success': False, 'message': 'Açıklama zorunlu!'})

        db = get_db()
        cursor = db.cursor()
        cursor.execute("""
            INSERT INTO duzeltme_talepleri
            (servis_id, personel_id, talep_aciklama, talep_tarihi, durum)
            VALUES (%s, %s, %s, NOW(), 'beklemede')
        """, (servis_id, session['personel_id'], talep))
        db.close()

        return jsonify({'success': True, 'message': '✅ Düzeltme talebi gönderildi!'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/yeni_teklif', methods=['GET', 'POST'])
@login_required
def yeni_teklif():
    if request.method == 'POST':
        try:
            db = get_db()
            cursor = db.cursor()

            musteri_id = request.form.get('musteri_id')
            konu = request.form.get('konu', '').strip()
            tarih = request.form.get('teklif_tarihi')
            kdv = int(request.form.get('kdv_orani', 18))
            notlar = request.form.get('notlar', '')
            iscilik = float(request.form.get('iscilik_ucreti', 0) or 0)

            # Teklif no oluştur
            yil = datetime.now().year
            cursor.execute(
                "SELECT MAX(CAST(SUBSTRING_INDEX(teklif_no, '-', -1) AS UNSIGNED)) as m FROM teklifler WHERE teklif_no LIKE %s",
                (f"ME-{yil}-%",)
            )
            r = cursor.fetchone()
            no = (r['m'] or 0) + 1
            teklif_no = f"ME-{yil}-{no:04d}"

            # Kalemleri topla
            aciklamalar = request.form.getlist('kalem_aciklama[]')
            miktarlar = request.form.getlist('kalem_miktar[]')
            birimler = request.form.getlist('kalem_birim[]')
            fiyatlar = request.form.getlist('kalem_fiyat[]')

            toplam = 0
            kalemler = []
            for i, ac in enumerate(aciklamalar):
                ac = ac.strip()
                if not ac:
                    continue
                miktar = float(miktarlar[i]) if i < len(miktarlar) else 1
                birim = birimler[i] if i < len(birimler) else 'adet'
                fiyat = float(fiyatlar[i]) if i < len(fiyatlar) else 0
                tp = round(miktar * fiyat, 2)
                toplam += tp
                kalemler.append({'aciklama': ac, 'miktar': miktar, 'birim': birim, 'fiyat': fiyat, 'toplam_fiyat': tp})

            # İşçilik kalemi olarak ekle
            if iscilik > 0:
                kalemler.append({'aciklama': 'İşçilik', 'miktar': 1, 'birim': 'iş', 'fiyat': iscilik, 'toplam_fiyat': iscilik})
                toplam += iscilik

            kdv_tutari = round(toplam * kdv / 100, 2)
            genel_toplam = round(toplam + kdv_tutari, 2)

            cursor.execute("""
                INSERT INTO teklifler
                (musteri_id, personel_id, teklif_tarihi, teklif_no, konu,
                 toplam_tutar, kdv_orani, kdv_tutari, genel_toplam,
                 teklif_durumu, notlar, kayit_tarihi)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'taslak', %s, NOW())
            """, (musteri_id, session['personel_id'], tarih, teklif_no, konu,
                  toplam, kdv, kdv_tutari, genel_toplam, notlar))

            teklif_id = cursor.lastrowid

            for idx, k in enumerate(kalemler, 1):
                cursor.execute("""
                    INSERT INTO teklif_detaylari
                    (teklif_id, aciklama, miktar, birim, fiyat, toplam_fiyat, sira_no)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (teklif_id, k['aciklama'], k['miktar'], k['birim'], k['fiyat'], k['toplam_fiyat'], idx))

            db.close()
            flash(f'✅ Teklif {teklif_no} kaydedildi!', 'success')
            return redirect(url_for('tekliflerim'))

        except Exception as e:
            flash(f'❌ Hata: {str(e)}', 'error')

    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT id, ad, soyad, sirket_adi FROM musteriler WHERE aktif=1 ORDER BY ad")
        musteriler = cursor.fetchall()
        db.close()
    except:
        musteriler = []

    today = datetime.now().strftime('%Y-%m-%d')
    return render_template('yeni_teklif.html', musteriler=musteriler, today=today)

@app.route('/tekliflerim')
@login_required
def tekliflerim():
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute("""
            SELECT t.id, t.teklif_no, t.teklif_tarihi, t.konu,
                   t.genel_toplam, t.teklif_durumu,
                   m.ad as musteri_ad, m.sirket_adi
            FROM teklifler t
            LEFT JOIN musteriler m ON t.musteri_id = m.id
            WHERE t.personel_id = %s
            ORDER BY t.kayit_tarihi DESC LIMIT 30
        """, (session['personel_id'],))
        teklifler = cursor.fetchall()
        db.close()
    except:
        teklifler = []

    return render_template('tekliflerim.html', teklifler=teklifler)

@app.route('/musteri_ara')
@login_required
def musteri_ara():
    q = request.args.get('q', '').strip()
    musteriler = []
    if q and len(q) >= 2:
        try:
            db = get_db()
            cursor = db.cursor()
            like = f'%{q}%'
            cursor.execute("""
                SELECT id, ad, soyad, sirket_adi, telefon, adres
                FROM musteriler
                WHERE aktif=1 AND (ad LIKE %s OR soyad LIKE %s OR sirket_adi LIKE %s OR telefon LIKE %s)
                ORDER BY ad LIMIT 20
            """, (like, like, like, like))
            musteriler = cursor.fetchall()
            db.close()
        except Exception as e:
            flash(f'Hata: {e}', 'error')
    return render_template('musteri_ara.html', musteriler=musteriler, q=q)

if __name__ == '__main__':
    run_migrations()
    port = int(os.getenv('PORT', 5001))
    app.run(debug=not os.getenv('RAILWAY_ENVIRONMENT'), host='0.0.0.0', port=port)
