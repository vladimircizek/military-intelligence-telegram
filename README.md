# Military Intelligence – Telegram bot

Soukromý Telegram bot pro denní vojensko-bezpečnostní přehled v češtině.

## Co umí
- `/report` – denní přehled z posledních 36 hodin
- `/urgent` – mimořádný přehled z posledních 24 hodin
- `/sources` – sledované zdroje
- `/help` – nápověda
- automatická zpráva každý den v 19:00 Europe/Prague

Zprávy rozlišují potvrzené informace, tvrzení stran konfliktu a neověřené informace. Výstup je analytické shrnutí otevřených zdrojů, nikoli zpravodajská služba ani nezávislé ověření událostí.

## Nasazení na Railway
1. V Telegramu otevři `@BotFather`, pošli `/newbot` a vytvoř nového bota.
2. Zkopíruj token. Nikam ho neposílej a nevkládej do GitHubu.
3. Nahraj tento projekt do nového GitHub repository.
4. V Railway vytvoř nový projekt → Deploy from GitHub Repo → vyber repository.
5. V Railway → Variables přidej proměnné z `.env.example`.
6. Po deploy otevři bota v Telegramu a stiskni Start.
7. Zjisti `TELEGRAM_CHAT_ID` a `TELEGRAM_ALLOWED_USER_ID` podle postupu níže, vlož je do Railway Variables a redeploy.

## Zjištění ID
Dočasně můžeš spustit tento příkaz v prohlížeči po nahrazení TOKEN:
`https://api.telegram.org/botTOKEN/getUpdates`

Nejdřív otevři chat s botem a stiskni Start, pak načti URL. V odpovědi najdeš `message.chat.id` a `message.from.id`. První je chat ID, druhé user ID. URL s tokenem nikomu neposílej. Po získání ID můžeš token v BotFather zneplatnit a vygenerovat nový, pokud jsi ho omylem sdílel.

## Důležité
- Pro denní plán musí proces běžet nepřetržitě jako Railway service; Railway nesmí službu uspávat.
- Zdroje jsou počáteční sada RSS kanálů; jejich dostupnost a úplnost je nutné ověřovat.
- OpenAI API je účtováno zvlášť podle použití.
- Nepřidávej tokeny ani API klíče do veřejného repozitáře.
