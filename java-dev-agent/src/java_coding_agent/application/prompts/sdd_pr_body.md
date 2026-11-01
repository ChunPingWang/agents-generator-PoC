請為以下功能撰寫 Pull Request 描述（Markdown，繁體中文），不要使用檔案區塊：

spec.md：
<spec>
{{ spec_md }}
</spec>

任務清單（全部已完成）：
{% for t in tasks %}- [x] {{ t.id }} {{ t.title }}
{% endfor %}

格式：
## 摘要
（兩到三句）
## 變更內容
（條列各層的主要類別）
## 測試
（條列測試類型與數量、Cucumber 場景）
## Tasks
（照上面清單原樣輸出 checklist）
