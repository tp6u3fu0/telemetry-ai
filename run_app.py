"""PyInstaller 打包進入點。

必須用絕對 import（`webapp.desktop`）——PyInstaller 把入口腳本當成 __main__
執行，若入口本身在套件內用相對 import 會因「無父套件」而崩潰。這層薄封裝讓
webapp.desktop 以套件模組被載入，其相對 import 才成立。

開發時仍可用 `uv run python -m webapp.desktop`（-m 會建立套件脈絡）。
"""
from webapp.desktop import main

if __name__ == "__main__":
    main()
