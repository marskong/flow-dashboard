
from datetime import datetime, timedelta, time
from google.appengine.ext import ndb
from google.appengine.api import mail
from constants import EVENT, USER, TASK, READABLE, JOURNALTAG
import tools
import json
import random
import logging
import re
import imp
try:
    imp.find_module('secrets')
except ImportError:
    import secrets_template as secrets
else:
    import secrets


class UserAccessible(ndb.Model):
    '''
    Parent class for items that have gated user access
    All UserAccessible models should have parent = owning user
    '''

    @classmethod
    def GetAccessible(cls, key_or_id, user, urlencoded_key=False):
        if key_or_id:
            if not urlencoded_key:
                key = ndb.Key.from_path(cls.__name__, key_or_id)
            else:
                key = ndb.Key(key_or_id)
            item = ndb.get(key)
            if item:
                if item.accessible(user):
                    return item
        return None

    def accessible(self, user):
        return self.parent == user.key


class User(ndb.Model):
    """
    Key - ID
    """
    name = ndb.StringProperty()
    email = ndb.StringProperty()
    pw_sha = ndb.StringProperty(indexed=False)
    pw_salt = ndb.StringProperty(indexed=False)
    create_dt = ndb.DateTimeProperty(auto_now_add=True)
    login_dt = ndb.DateTimeProperty(auto_now_add=True)
    level = ndb.IntegerProperty(default=USER.USER)
    gender = ndb.StringProperty()
    timezone = ndb.StringProperty(default="UTC", indexed=False)
    birthday = ndb.DateProperty()
    # Google
    integrations = ndb.TextProperty()  # Flat JSON dict
    settings = ndb.TextProperty()  # JSON
    g_id = ndb.StringProperty()

    def __str__(self):
        return self.name if self.name else self.email

    def json(self, is_self=False):
        return {
            'id': self.key.id(),
            'name': self.name,
            'email': self.email,
            'integrations': tools.getJson(self.integrations),
            'settings': tools.getJson(self.settings, {}),
            'timezone': self.timezone,
            'birthday': tools.iso_date(self.birthday) if self.birthday else None
        }

    @staticmethod
    def GetByEmail(email):
        u = User.query().filter(User.email == email.lower()).get()
        return u

    @staticmethod
    def GetByGoogleId(id):
        u = User.query().filter(User.g_id == id).get()
        return u

    @staticmethod
    def Create(email=None, g_id=None, name=None, password=None):
        from constants import ADMIN_EMAIL, SENDER_EMAIL, SITENAME, \
            APP_OWNER, DEFAULT_USER_SETTINGS
        if email or g_id:
            u = User(email=email.lower() if email else None, g_id=g_id, name=name)
            if email.lower() == APP_OWNER:
                u.level = USER.ADMIN
            u.setPass(password)
            u.Update(settings=DEFAULT_USER_SETTINGS)
            if not tools.on_dev_server():
                mail.send_mail(to=ADMIN_EMAIL, sender=SENDER_EMAIL,
                               subject="[ %s ] New User - %s" % (SITENAME, email),
                               body="That's all")
            return u
        return None

    def Update(self, **params):
        if 'name' in params:
            self.name = params.get('name')
        if 'timezone' in params:
            self.timezone = params.get('timezone')
        if 'birthday' in params:
            self.birthday = tools.fromISODate(params.get('birthday'))
        if 'settings' in params:
            self.settings = json.dumps(params.get('settings'), {})

    def admin(self):
        return self.level == USER.ADMIN

    def setPass(self, pw=None):
        if not pw:
            pw = tools.GenPasswd(length=6)
        self.pw_salt, self.pw_sha = tools.getSHA(pw)
        return pw

    def checkPass(self, pw):
        pw_salt, pw_sha = tools.getSHA(pw, salt=self.pw_salt)
        return self.pw_sha == pw_sha

    def get_timezone(self):
        return self.timezone if self.timezone else "UTC"

    def first_name(self):
        if self.name:
            return self.name.split(' ')[0]

    def checkToken(self, token):
        return self.session_id_token == token or self.session_id_token == unicode(quopri.decodestring(token), 'iso_8859-2')

    def get_integration_prop(self, prop, default=None):
        integrations = tools.getJson(self.integrations)
        if integrations:
            return integrations.get(prop, default)
        return default

    def set_integration_prop(self, prop, value):
        integrations = tools.getJson(self.integrations)
        if not integrations:
            integrations = {}
        integrations[prop] = value
        self.integrations = json.dumps(integrations)

    def aes_access_token(self, client_id='google'):
        from common.aes_cypher import AESCipher
        cypher = AESCipher(secrets.AES_CYPHER_KEY)
        msg = cypher.encrypt(json.dumps({
            'client_id': client_id,
            'user_id': self.key.id()
            }))
        return msg

    @staticmethod
    def user_id_from_aes_access_token(access_token):
        from common.aes_cypher import AESCipher
        cypher = AESCipher(secrets.AES_CYPHER_KEY)
        raw = cypher.decrypt(access_token)
        loaded = tools.getJson(raw)
        if loaded:
            return loaded.get('user_id')


class Project(UserAccessible):
    """
    Ongoing projects with links

    Key - ID
    """
    dt_created = ndb.DateTimeProperty(auto_now_add=True)
    dt_completed = ndb.DateTimeProperty()
    dt_archived = ndb.DateTimeProperty()
    urls = ndb.TextProperty(repeated=True)
    title = ndb.TextProperty()
    subhead = ndb.TextProperty()
    starred = ndb.BooleanProperty(default=False)
    archived = ndb.BooleanProperty(default=False)
    progress = ndb.IntegerProperty(default=0) # 1 - 10 (-1 disabled)
    progress_ts = ndb.IntegerProperty(repeated=True, indexed=False) # Timestamp (ms) for each progress step (len 10)

    def json(self):
        return {
            'id': self.key.id(),
            'ts_created': tools.unixtime(self.dt_created),
            'ts_completed': tools.unixtime(self.dt_completed),
            'ts_archived': tools.unixtime(self.dt_archived),
            'title': self.title,
            'subhead': self.subhead,
            'progress': self.progress,
            'progress_ts': self.progress_ts,
            'archived': self.archived,
            'starred': self.starred,
            'urls': self.urls
        }

    @staticmethod
    def Active(user):
        return Project.query(ancestor=user.key).filter(Project.archived == False).order(Project.starred).order(-Project.dt_created).fetch(limit=20)

    @staticmethod
    def Fetch(user):
        return Project.query(ancestor=user.key).order(-Project.dt_created).fetch(limit=20)

    @staticmethod
    def Create(user):
        return Project(parent=user.key)

    def Update(self, **params):
        if 'urls' in params:
            self.urls = params.get('urls')
        if 'title' in params:
            self.title = params.get('title')
        if 'subhead' in params:
            self.subhead = params.get('subhead')
        if 'starred' in params:
            self.starred = params.get('starred')
        if 'archived' in params:
            change = self.archived != params.get('archived')
            self.archived = params.get('archived')
            if change and self.archived:
                self.dt_archived = datetime.now()
        if 'progress' in params:
            self.set_progress(params.get('progress'))

    def set_progress(self, progress):
        if not self.progress_ts:
            self.progress_ts = [0 for x in range(10)] # Initialize
        self.progress_ts[progress-1] = tools.unixtime()
        changed = progress != self.progress
        self.progress = progress
        if changed and self.is_completed():
            self.dt_completed = datetime.now()
        return changed

    def is_completed(self):
        return self.progress == 10


class Task(UserAccessible):
    """
    Tasks (currently not linked with projects)
    For tracking daily 'top tasks'

    Key - ID
    """
    dt_created = ndb.DateTimeProperty(auto_now_add=True)
    dt_due = ndb.DateTimeProperty()
    dt_done = ndb.DateTimeProperty()
    title = ndb.TextProperty()
    status = ndb.IntegerProperty(default=TASK.NOT_DONE)
    wip = ndb.BooleanProperty(default=False)
    archived = ndb.BooleanProperty(default=False)

    def json(self):
        return {
            'id': self.key.id(),
            'ts_created': tools.unixtime(self.dt_created),
            'ts_due': tools.unixtime(self.dt_due),
            'ts_done': tools.unixtime(self.dt_done),
            'status': self.status,
            'archived': self.archived,
            'wip': self.wip,
            'title': self.title,
            'done': self.is_done()
        }

    @staticmethod
    def Open(user, limit=10):
        return Task.query(ancestor=user.key).filter(Task.status == TASK.NOT_DONE).order(-Task.dt_created).fetch(limit=limit)

    @staticmethod
    def Recent(user, limit=10):
        return Task.query(ancestor=user.key).filter(Task.archived == False).order(-Task.dt_created).fetch(limit=limit)

    @staticmethod
    def DueInRange(user, start, end, limit=100):
        q = Task.query(ancestor=user.key).order(-Task.dt_due)
        if start:
            q = q.filter(Task.dt_due >= start)
        if end:
            q = q.filter(Task.dt_due <= end)
        return q.fetch(limit=limit)

    @staticmethod
    def Create(user, title, due=None):
        if not due:
            due = datetime.now() + timedelta(days=1)
        return Task(title=title, dt_due=due, parent=user.key)

    def Update(self, **params):
        from constants import TASK_DONE_REPLIES
        message = None
        if 'title' in params:
            self.title = params.get('title')
        if 'status' in params:
            change = self.status != params.get('status')
            self.status = params.get('status')
            if change and self.is_done():
                self.dt_done = datetime.now()
                self.wip = False
                message = random.choice(TASK_DONE_REPLIES)
        if 'archived' in params:
            self.archived = params.get('archived')
            if self.archived:
                self.wip = False
        if 'wip' in params:
            self.wip = params.get('wip')
        return message

    def is_done(self):
        return self.status == TASK.DONE


class Habit(UserAccessible):
    """
    Key - ID
    """
    dt_created = ndb.DateTimeProperty(auto_now_add=True)
    name = ndb.TextProperty()
    color = ndb.TextProperty()
    tgt_weekly = ndb.IntegerProperty(indexed=False)
    archived = ndb.BooleanProperty(default=False)
    icon = ndb.TextProperty()

    def json(self):
        return {
            'id': self.key.id(),
            'ts_created': tools.unixtime(self.dt_created),
            'name': self.name,
            'color': self.color,
            'archived': self.archived,
            'tgt_weekly': self.tgt_weekly,
            'icon': self.icon
        }

    @staticmethod
    def All(user):
        return Habit.query(ancestor=user.key).fetch(limit=20)

    @staticmethod
    def Active(user):
        return Habit.query(ancestor=user.key).filter(Habit.archived == False).fetch(limit=5)

    @staticmethod
    def Create(user):
        return Habit(parent=user.key)

    def Update(self, **params):
        if 'name' in params:
            self.name = params.get('name')
        if 'color' in params:
            self.color = params.get('color')
        if 'icon' in params:
            self.icon = params.get('icon')
        if 'archived' in params:
            self.archived = params.get('archived')
        if 'tgt_weekly' in params:
            self.tgt_weekly = params.get('tgt_weekly')


class HabitDay(UserAccessible):
    """
    Key - ID: habit:[habit_id]_day:[iso_date]
    """
    dt_created = ndb.DateTimeProperty(auto_now_add=True)
    dt_updated = ndb.DateTimeProperty(auto_now_add=True)
    habit = ndb.KeyProperty(Habit)
    date = ndb.DateProperty()
    done = ndb.BooleanProperty(default=False)
    committed = ndb.BooleanProperty(default=False)

    def json(self):
        return {
            'id': self.key.id(),
            'ts_created': tools.unixtime(self.dt_created),
            'ts_updated': tools.unixtime(self.dt_updated),
            'habit_id': self.habit.id(),
            'done': self.done,
            'committed': self.committed
        }

    @staticmethod
    def Range(user, habits, since_date, until_date=None):
        today = datetime.today()
        if not until_date:
            until_date = today
        cursor = since_date
        ids = []
        while cursor <= until_date:
            for h in habits:
                ids.append(ndb.Key('HabitDay', HabitDay.ID(h, cursor), parent=user.key))
            cursor += timedelta(days=1)
        if ids:
            return ndb.get_multi(ids)
        return []

    @staticmethod
    def ID(habit, date):
        return "habit:%s_day:%s" % (habit.key.id(), tools.iso_date(date))

    @staticmethod
    def Create(user, habit, date):
        id = HabitDay.ID(habit, date)
        return HabitDay(id=id, habit=habit.key, date=date, parent=user.key)

    def Update(self, **params):
        if 'done' in params:
            self.done = params.get('done')

    @staticmethod
    def Toggle(habit, date, force_done=False):
        id = HabitDay.ID(habit, date)
        hd = HabitDay.get_or_insert(id,
                                    habit=habit.key,
                                    date=date,
                                    parent=habit.key.parent())
        if not force_done or not hd.done:
            # If force_done, only toggle if not done
            hd.toggle()
        hd.put()
        return (hd.done, hd)

    @staticmethod
    def Commit(habit, date=None):
        if not date:
            date = datetime.today()
        id = HabitDay.ID(habit, date)
        hd = HabitDay.get_or_insert(id,
                                    habit=habit.key,
                                    date=date,
                                    parent=habit.key.parent())
        hd.commit()
        hd.put()
        return hd

    def toggle(self):
        self.dt_updated = datetime.now()
        self.done = not self.done
        return self.done

    def commit(self):
        if not self.done:
            self.committed = True


class JournalTag(UserAccessible):
    """
    Stores frequent activities/tags/people for daily journal

    Key - ID: Full tag with @/#, e.g. '#DinnerOut', '@BarackObama'
    """
    dt_added = ndb.DateTimeProperty(auto_now_add=True)
    name = ndb.TextProperty()
    type = ndb.IntegerProperty(default=JOURNALTAG.PERSON)

    def json(self):
        return {
            'id': self.key.id(),
            'name': self.name,
            'type': self.type
        }

    @staticmethod
    def All(user, limit=400):
        return JournalTag.query(ancestor=user.key).fetch(limit=limit)

    @staticmethod
    def Key(user, name, prefix='@'):
        if name:
            name = tools.capitalize(name)
            return ndb.Key('JournalTag', prefix+name, parent=user.key)

    @staticmethod
    def CreateFromText(user, text):
        people = re.findall(r'@([a-zA-Z]{3,30})', text)
        hashtags = re.findall(r'#([a-zA-Z]{3,30})', text)
        new_jts = []
        all_jts = []
        logging.debug("Found %d @mentions: %s" % (len(people), people))
        logging.debug("Found %d #tags: %s" % (len(hashtags), hashtags))
        people_ids = [JournalTag.Key(user, p) for p in people]
        hashtag_ids = [JournalTag.Key(user, ht, prefix='#') for ht in hashtags]
        existing_tags = ndb.get_multi(people_ids + hashtag_ids)
        for existing_tag, key in zip(existing_tags, people_ids + hashtag_ids):
            if not existing_tag:
                prefix = key.id()[0]
                type = JOURNALTAG.HASHTAG if prefix == '#' else JOURNALTAG.PERSON
                jt = JournalTag(id=key.id(), name=key.id()[1:], type=type, parent=user.key)
                new_jts.append(jt)
                all_jts.append(jt)
            else:
                all_jts.append(existing_tag)
        ndb.put_multi(new_jts)
        return all_jts

    def person(self):
        return self.type == JOURNALTAG.PERSON


class MiniJournal(UserAccessible):
    """
    Key - ID: [ISO_date]
    Capture some basic data points from the day via 1-2 questions?
    Questions defined on client side.

    Optionally collect and track completion of top 3 tasks (decided tonight for tomorrow)
    """
    date = ndb.DateProperty()  # Date for entry
    dt_created = ndb.DateTimeProperty(auto_now_add=True)
    data = ndb.TextProperty()  # JSON (keys are data names, values are responses)
    tags = ndb.KeyProperty(repeated=True)  # IDs of JournalTags()
    location = ndb.GeoPtProperty()

    def json(self):
        res = {
            'id': self.key.id(),
            'iso_date': tools.iso_date(self.date),
            'data': tools.getJson(self.data),
            'tags': [tag.id() for tag in self.tags]
        }
        if self.location:
            res.update({
                'lat': self.location.lat,
                'lon': self.location.lon
            })
        return res

    @staticmethod
    def Create(user, date):
        id = tools.iso_date(date)
        return MiniJournal(id=id, date=date, parent=user.key)

    @staticmethod
    def Get(user, date):
        id = tools.iso_date(date)
        return MiniJournal.get_by_id(id, parent=user.key)

    def Update(self, **params):
        if 'data' in params:
            self.data = json.dumps(params.get('data'))
        if 'lat' in params and 'lon' in params:
            gp = ndb.GeoPt("%s, %s" % (params.get('lat'), params.get('lon')))
            self.location = gp
        if 'tags' in params:
            self.tags = params.get('tags', [])


class Event(UserAccessible):
    """
    Key - ID

    Events (single date or ranges) that are meaningful
    """
    date_start = ndb.DateProperty()
    date_end = ndb.DateProperty()
    title = ndb.TextProperty()
    details = ndb.TextProperty()
    color = ndb.StringProperty()  # Hex
    private = ndb.BooleanProperty(default=True)
    type = ndb.IntegerProperty(default=EVENT.PERSONAL)

    def json(self):
        res = {
            'id': self.key.id(),
            'date_start': tools.iso_date(self.date_start),
            'date_end': tools.iso_date(self.date_end),
            'title': self.title,
            'details': self.details,
            'color': self.color,
            'private': self.private,
            'single': self.single(),
            'type': self.type
        }
        return res

    @staticmethod
    def Fetch(user, limit=20, offset=0):
        return Event.query(ancestor=user.key).order(Event.date_start).fetch(limit=limit, offset=offset)

    @staticmethod
    def Create(user, date_start, date_end=None, title=None):
        if not date_end:
            date_end = date_start
        return Event(date_start=date_start, date_end=date_end, title=title, parent=user.key)

    def Update(self, **params):
        if 'title' in params:
            self.title = params.get('title')
        if 'details' in params:
            self.details = params.get('details')
        if 'color' in params:
            self.color = params.get('color')
        if 'date_start' in params:
            self.date_start = params.get('date_start')
        if 'date_end' in params:
            self.date_end = params.get('date_end')
        if 'type' in params:
            self.type = params.get('type')

    def single(self):
        return self.date_start == self.date_end


class Goal(UserAccessible):
    """
    Key - ID: [YYYY] if annual goal, [YYYY-MM] if monthly goal

    Annual/monthly goals (currently captured in timeline gsheet)

    """
    date = ndb.DateProperty()  # Date for (first day of month or year)
    dt_created = ndb.DateTimeProperty(auto_now_add=True)
    text = ndb.TextProperty(repeated=True)  # Can have multiple goals for period
    assessment = ndb.IntegerProperty()  # How'd we do (1-5)

    def json(self):
        res = {
            'id': self.key.id(),
            'iso_date': tools.iso_date(self.date),
            'text': self.text,
            'assessment': self.assessment,
            'annual': self.annual()
        }
        if not self.annual():
            res['month'] = self.date.month
        return res

    @staticmethod
    def Recent(user):
        goals = Goal.query(ancestor=user.key).order(-Goal.dt_created).fetch(limit=13)
        return goals

    @staticmethod
    def Year(user, year):
        jan_1 = datetime(year, 1, 1).date()
        goals = Goal.query(ancestor=user.key).filter(Goal.date >= jan_1).fetch(limit=13)
        return sorted(filter(lambda g: g.date.year == year and not g.annual(), goals),
            key=lambda g: g.date)

    @staticmethod
    def Current(user, which="both"):
        date = datetime.today()
        keys = []
        if which in ["both", "year"]:
            annual_id = ndb.Key('Goal', datetime.strftime(date, "%Y"), parent=user.key)
            keys.append(annual_id)
        if which in ["both", "month"]:
            monthly_id = ndb.Key('Goal', datetime.strftime(date, "%Y-%m"), parent=user.key)
            keys.append(monthly_id)
        goals = ndb.get_multi(keys)
        return [g for g in goals]

    @staticmethod
    def Create(user, id=None, date=None, annual=False):
        if not id:
            if not annual:
                id = datetime.strftime(date, "%Y-%m")
            else:
                id = datetime.strftime(date, "%Y")
        if not annual:
            if not date:
                first_of_month = tools.fromISODate(id + "-01")
                date = first_of_month
        else:
            if not date:
                first_of_year = datetime(int(id), 1, 1)
                date = first_of_year
        return Goal(id=id, date=date, parent=user.key)

    def Update(self, **params):
        if 'text' in params:
            self.text = params.get('text')
        if 'assessment' in params:
            a = params.get('assessment')
            if a:
                self.assessment = int(a)

    def type(self):
        return 'annual' if self.annual() else 'monthly'

    def annual(self):
        return len(self.key.id()) == 4


class Productivity(UserAccessible):
    """
    Key - ID: [YYYY-MM-DD]

    Productivity data for a day (e.g. git commits)

    """
    date = ndb.DateProperty()
    dt_created = ndb.DateTimeProperty(auto_now_add=True)
    data = ndb.TextProperty()  # Flat JSON object

    def json(self):
        return {
            'id': self.key.id(),
            'iso_date': tools.iso_date(self.date),
            'data': tools.getJson(self.data)
        }

    @staticmethod
    def Create(user, date):
        id = tools.iso_date(date)
        return Productivity.get_or_insert(id, date=date, parent=user.key)

    @staticmethod
    def Range(user, dt_start, dt_end):
        return Productivity.query(ancestor=user.key).order(-Productivity.date) \
            .filter(Productivity.date >= dt_start) \
            .filter(Productivity.date <= dt_end) \
            .fetch()

    def Update(self, **params):
        if 'data' in params:
            self.data = json.dumps(params.get('data'))


class Readable(UserAccessible):
    """
    Readable things (books / articles)

    Key - ID [source]:[source id]

    """
    source_id = ndb.TextProperty()
    dt_added = ndb.DateTimeProperty()
    dt_read = ndb.DateTimeProperty()
    title = ndb.TextProperty()  # Can have multiple goals for period
    author = ndb.TextProperty()
    image_url = ndb.TextProperty()
    url = ndb.TextProperty()
    favorite = ndb.BooleanProperty(default=False)
    type = ndb.IntegerProperty(default=READABLE.ARTICLE)
    excerpt = ndb.TextProperty()
    source = ndb.TextProperty()  # e.g. 'pocket', 'goodreads'
    tags = ndb.TextProperty(repeated=True)
    read = ndb.BooleanProperty(default=False)
    word_count = ndb.IntegerProperty()

    def json(self):
        return {
            'id': self.key.id(),
            'title': self.title,
            'author': self.author,
            'favorite': self.favorite,
            'image_url': self.image_url,
            'url': self.url,  # Original url
            'source_url': self.get_source_url(),
            'type': self.type,
            'source': self.source,
            'read': self.read,
            'word_count': self.word_count
        }

    @staticmethod
    def Unread(user, limit=30):
        readables = Readable.query(ancestor=user.key).filter(Readable.read == False).order(-Readable.dt_added).fetch(limit=limit)
        return readables

    @staticmethod
    def CreateOrUpdate(user, source_id, title=None, url=None,
                       type=READABLE.ARTICLE, source=None,
                       author=None, image_url=None, excerpt=None,
                       tags=None, read=False, favorite=False,
                       dt_read=None,
                       word_count=0, dt_added=None):
        if title and source:
            if tags is None:
                tags = []
            if not dt_added:
                dt_added = datetime.now()
            id = source + ':' + source_id
            r = Readable.get_or_insert(id, parent=user.key, source_id=source_id, title=title,
                            url=url,
                            type=type, source=source, read=read, dt_added=dt_added,
                            excerpt=excerpt, favorite=favorite, tags=tags, dt_read=dt_read,
                            image_url=image_url, author=author, word_count=word_count)
            return r

    def Update(self, **params):
        if 'read' in params:
            change = self.read != params.get('read')
            self.read = params.get('read')
            if change and self.read:
                self.dt_read = datetime.now()
        if 'favorite' in params:
            self.favorite = params.get('favorite')
        if 'dt_read' in params:
            self.dt_read = params.get('dt_read')
        if 'source' in params:
            self.source = params.get('source')
        if 'excerpt' in params:
            self.excerpt = params.get('excerpt')
        if 'title' in params:
            self.title = params.get('title')
        if 'url' in params:
            self.url = params.get('url')
        if 'type' in params:
            self.type = params.get('type')
        if 'source_id' in params:
            self.source_id = params.get('source_id')
        if 'image_url' in params:
            self.image_url = params.get('image_url')
        if 'tags' in params:
            self.tags = params.get('tags')
        if 'author' in params:
            self.author = params.get('author')
        if 'word_count' in params:
            self.word_count = params.get('word_count')

    def get_source_url(self):
        if self.source == 'pocket':
            return "https://getpocket.com/a/read/%s" % self.source_id