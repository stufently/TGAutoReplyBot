MANAGER_CHAT = поменять на ту группу куда надо отправлять в енве


- [Check money usage](https://platform.openai.com/settings/organization/usage)
- [Check assistant prompt and assistant ID](https://platform.openai.com/playground/assistants?assistant=asst_a64KsnmW6oGfjQcs89kB7rt1)
- Don't forget to update prompts in the repo first, and only then update them [here](https://platform.openai.com/playground/assistants?assistant=asst_a64KsnmW6oGfjQcs89kB7rt1).


### 1. Create Required Directories

```bash
mkdir tdatas sessions
```
2. Copy Your Telegram `tdata`


### 3. Login to Docker Registry

```bash
docker login registry.gitlab.9qw.ru:5005/9qw/autootvetchikchatgpt:latest
```

### 4. Run with Docker Compose

```bash
docker-compose up -d
```

To update:
```bash
docker-compose pull
docker-compose down
docker-compose up -d
```

###  Запуск
в енв поменять MANAGER_CHAT на ту группу которую используете и запушьте его в репо
```bash
docker-compose up-d
```


### Процесс обновления промта (Ассистенты)
- в [Playground](https://platform.openai.com/playground/assistants?assistant=asst_vjWizQjt06NVFYtHwS6OX3b1) нет никакого учёта изменений, всё делается в репозитории,
нужно сначала изменить промт в самом [РЕПОЗИТОРИИ](https://gitlab.9qw.ru/9qw/autootvetchikchatgpt/-/blob/main/promts/autootventchik.txt?ref_type=heads), далее во вкладке Assistants, в поле "System instructions", нужно старый промт, 
заменить на новый, предварительно выбрав ассисента, которому хотим поменять промпт.


### Примечание
для софта надо сделать отдельную tdata сессию, чтобы не могло быть запущено
две одинаковые, иначе их будет выбивать