# Modul Load Cell HX711 (Raspberry Pi 5)

Direktori ini berisi pustaka dan skrip pengujian pembacaan sensor berat **HX711** secara langsung pada **Raspberry Pi 5** menggunakan chip GPIO `gpiochip4` (`gpiod` / `lgpio`).

## Konfigurasi Pin (BCM)

| Sinyal HX711 | Pin BCM Raspberry Pi 5 | Pin Fisik Header | Keterangan |
| :--- | :--- | :--- | :--- |
| **VCC** | 3.3V | Pin 1 / Pin 17 | Tegangan referensi logika |
| **GND** | GND | Pin 6 / Pin 9 | Ground |
| **DT (DOUT)** | **GPIO 5** | Pin 29 | Jalur data serial HX711 |
| **SCK (PD_SCK)** | **GPIO 6** | Pin 31 | Jalur clock serial HX711 |

## Struktur File
- `loadcell.py`: Kelas driver `HX711` dengan fitur pembacaan median, *zero offset (tare)*, dan konversi ke satuan gram.
- `test_loadcell.py`: Skrip CLI interaktif untuk menguji pembacaan mentah (*raw ADC*) dan memverifikasi kalibrasi beban.

## Prosedur Kalibrasi (`SCALE`)
1. Pastikan timbangan kosong saat program dimulai untuk melakukan **Tare** otomatis.
2. Letakkan beban referensi yang diketahui beratnya (misal anak timbangan `500 g`).
3. Catat selisih nilai `raw` terhadap `offset`, lalu perbarui konstanta `SCALE` dengan rumus:
   ```text
   SCALE = (raw_beban - offset) / berat_referensi_gram
   ```
