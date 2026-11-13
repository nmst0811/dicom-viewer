# DicomViewer

医用画像処理実験 最終課題。Python + PySide6 で作った、DICOM CT の3断面ビューア（Axial / Coronal / Sagittal）です。
既製の DICOM ビューアは使わず、画像の描画・ウィンドウ処理・位置線の描画をすべて自作しています。

## 動作環境

| 項目 | 内容 |
| --- | --- |
| OS | Windows 11 64bit |
| Python | 3.13（64bit）。`py -3.13 --version` で確認できます |
| 画面 | 1366×768 以上を推奨（ウィンドウの最小サイズは 1200×600） |
| ネットワーク | 導入時の `pip install` でのみ必要です |

DICOM データは同梱していません。授業で配布された CT のフォルダを使ってください。

## 導入手順

**ZIP は、`C:\DicomViewer` のような短いパスに展開してください。** 展開先フォルダのパスが 100 文字を超えると、`pip install` が失敗します（Windows のパス長の制限。詳しくは [トラブルシュート](#トラブルシュート) の 4）。

PowerShell で、展開したフォルダ（この `README.md` と `main.py` があるフォルダ）に移動して、次を順に実行します。

```powershell
py -3.13 -m venv venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

- 2 行目の `Set-ExecutionPolicy` は、今開いている PowerShell のウィンドウだけ、スクリプト（`Activate.ps1`）の実行を許可する設定です（PC の設定は変わりません）。Windows 11 の既定では、これが無いと `Activate.ps1` が拒否されます。詳しくは [トラブルシュート](#トラブルシュート) の 1 を見てください。
- インストールには数分かかります（PySide6・VTK は合計で数百 MB）。
- 最後の `pip install` が `Successfully installed ...` で終われば成功です。`ERROR` で終わった場合は、[トラブルシュート](#トラブルシュート) を見てください。

## 起動手順

導入手順の続き（`(venv)` が表示されている状態）で、次を実行します。

```powershell
python main.py
```

次回以降は、PowerShell を開いてこのフォルダに移動し、次の 3 行で起動できます（`venv` は作り直さなくて構いません）。

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\venv\Scripts\Activate.ps1
python main.py
```

## 操作方法

### 1. DICOM を読み込む（ファイルメニュー）

1. メニューの **ファイル → DICOMフォルダを開く**（`Ctrl+O`）を選び、DICOM が入ったフォルダを選択します。
2. サブフォルダも含めて検索し、DICOM 以外のファイルは無視します。
3. 複数のシリーズ（SeriesInstanceUID）が見つかったときは、枚数の多い順に並んだ一覧が出ます。読み込むシリーズを選んで **読み込む** を押します。
4. 読み込み中は進捗が表示されます（**キャンセル** で中止できます）。
5. 失敗したときはエラーのダイアログが出ます。アプリは終了せず、表示中のデータもそのまま残ります。

読み込み後、3 つの断面が表示されます。ステータスバー（ウィンドウ下端）には、画像サイズ・枚数・ピクセル間隔が出ます。
終了は **ファイル → 終了**（`Ctrl+Q`）です。

### 2. 3 断面の画面

横に 3 列で、左から **Axial（体軸断）・Coronal（冠状断）・Sagittal（矢状断）** です。
各列の上のラベル（例: `Axial  z = 67 / 133`）は、現在のスライス位置と全体の枚数です。
Coronal と Sagittal は、頭側が上になる向きで表示し、スライス間隔を使って縦横の比率を実際の大きさに合わせています。

### 3. スライダー

各列の画像の下に、スライダーが 3 本あります。3 つの断面で、それぞれ独立に動きます。

| スライダー | 範囲 | 内容 |
| --- | --- | --- |
| スライス | 1〜枚数 | その断面のスライス位置を変えます |
| WL（ウィンドウレベル） | -1000〜1000 HU | 右端に現在値を表示します |
| WW（ウィンドウ幅） | 1〜4000 HU | 右端に現在値を表示します |

- WL・WW は、操作した列の画像だけに反映されます（他の列には影響しません）。
- 初期値は WL = -600 HU、WW = 1500 HU（肺野条件）です。スライス位置は各軸の中央です。

### 4. 位置を示す緑の線

各画像には、**他の 2 断面の位置**を示す緑の線（太さ 1px）が表示されます。

| 画像 | 横線 | 縦線 |
| --- | --- | --- |
| Axial | Coronal の位置（j） | Sagittal の位置（i） |
| Coronal | Axial の位置（k） | Sagittal の位置（i） |
| Sagittal | Axial の位置（k） | Coronal の位置（j） |

スライダーを動かすと、他の列の線がそれに連動して動きます。
（追加機能）画像の上を **左クリック**、または **左ボタンを押したままドラッグ** すると、その点が 3 断面の交点になるように全断面が移動します。

## 評価観点と確認方法

| 観点 | 確認方法 |
| --- | --- |
| 1. スライス画像を表示する自作 Viewer | 起動して DICOM を読み込むと、自作ウィジェット（`viewer/slice_view.py`）が画像を描画します |
| 2. ファイルメニューでデータを選択 | ファイル → DICOMフォルダを開く（`Ctrl+O`） |
| 3. Axial / Coronal / Sagittal を表示 | 読み込み後、横 3 列に表示されます |
| 4. 他の 2 断面の位置を示す線 | 各画像上の緑の線 |
| 5. WL・WW・スライス位置をスライダーで変更 | 各列の下の 3 本のスライダー |
| 6. 線がスライダーに連動 | スライスのスライダーを動かすと、他の列の線が動きます |

## 対応データ形式

- DICOM の CT 画像（1 ファイル = 1 枚のスライス）。拡張子は不問で、サブフォルダ内のファイルも対象です。
- スライスは `ImagePositionPatient` の z 座標の昇順に並べます（無い場合は `InstanceNumber`）。
- 画素値は `HU = pixel × RescaleSlope + RescaleIntercept` で HU に変換します。
- 間隔は、x・y を `PixelSpacing`、z を隣り合うスライスの z 座標の差から求めます（`SliceThickness` より優先）。
- 動作確認は、非圧縮の DICOM（LIDC-IDRI の CT、512×512×133 枚）で行っています。JPEG 系の圧縮 DICOM は、`pylibjpeg` で読み込めるようにしてありますが、動作確認はしていません。
- マルチフレーム形式（1 ファイルに複数枚）、カラー画像、スライスごとに画像サイズが違うシリーズは、読み込めません（エラーのダイアログが出ます）。

## トラブルシュート

### 1. `Activate.ps1` が「このシステムではスクリプトの実行が無効になっているため…」で拒否される

PowerShell の実行ポリシーが原因です（Windows 11 の既定では、スクリプトの実行が禁止されています）。導入手順の 2 行目を実行していない場合は、次のどちらかで解決します。

- **今開いている PowerShell だけ許可する**（設定は残りません。おすすめ）

  ```powershell
  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
  .\venv\Scripts\Activate.ps1
  ```

- **有効化（Activate）せずに実行する**

  ```powershell
  .\venv\Scripts\python.exe -m pip install -r requirements.txt
  .\venv\Scripts\python.exe main.py
  ```

### 2. `py -3.13` が見つからない（`No suitable Python runtime found`）

Python 3.13（64bit）が入っていません。`py -0p` で、入っている Python の一覧を確認できます。
[python.org](https://www.python.org/downloads/) から Python 3.13 をインストールするか、次を実行してください。

```powershell
winget install Python.Python.3.13
```

インストール後は、PowerShell を開き直してください。

### 3. `pip install` が失敗する・止まる

ネットワークに接続できているか確認してください（プロキシ環境では、設定が必要なことがあります）。
次を実行してから、もう一度 `pip install -r requirements.txt` を試してください。

```powershell
python -m pip install --upgrade pip
```

### 4. `pip install` が `OSError: [Errno 2] No such file or directory: '...\PySide6\qml\...'` で失敗する（パスが長い）

Windows の既定では、260 文字を超えるパスのファイルを作れません。PySide6 には長い名前のファイルがあるため、展開先フォルダのパスが長い（約 117 文字を超える）と、インストール中にこのエラーになります。

展開先フォルダのパスの長さは、そのフォルダで次を実行すると確認できます（100 以下にしてください）。

```powershell
(Get-Location).Path.Length
```

`venv` はフォルダを移動できません。次の手順で、短いパスに作り直してください。

1. このフォルダ全体を、`C:\DicomViewer` のような短いパスに移す（または ZIP を展開し直す）
2. 失敗した `venv` フォルダがあれば削除する（`Remove-Item -Recurse -Force .\venv`）
3. 移したフォルダで、導入手順を最初からやり直す

### 5. 起動時に `ImportError: DLL load failed` と出る

Microsoft Visual C++ 再頒布可能パッケージ（x64）が入っていない可能性があります。
[Microsoft の配布ページ](https://learn.microsoft.com/ja-jp/cpp/windows/latest-supported-vc-redist) から、x64 版をインストールしてください。

### 6. 「選択したフォルダに DICOM 画像が見つかりませんでした」と出る

DICOM が入っているフォルダ（またはその親フォルダ）を選んでいるか確認してください。サブフォルダも検索します。
ファイルに DICOM のヘッダ（先頭 128 バイトの後ろの `DICM`）が無い古い形式は、DICOM として認識されません。

### 7. 画像が真っ黒・真っ白になる

WL・WW スライダーの値が、そのデータに合っていない可能性があります。データを開き直すと、初期値（WL = -600、WW = 1500）に戻ります。
