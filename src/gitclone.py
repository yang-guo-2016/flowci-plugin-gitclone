import os
import sys
import urllib
import shutil
import threading
from datetime import datetime
from threading import Thread, Event
from git import Repo
from git import RemoteProgress
from flowci import client, domain

# inputs of plugin
GitUrl = client.GetVar('FLOWCI_GIT_URL')
GitRepoName = client.GetVar('FLOWCI_GIT_REPO')
GitBranch = client.GetVar('FLOWCI_GIT_BRANCH', False)
GitCommitId = client.GetVar('FLOWCI_GIT_COMMIT_ID', False)
GitTimeOut = int(client.GetVar('FLOWCI_GITCLONE_TIMEOUT'))

VarAuthor = "FLOWCI_GIT_AUTHOR"
VarCommitID = "FLOWCI_GIT_COMMIT_ID"
VarCommitMessage = "FLOWCI_GIT_COMMIT_MESSAGE"
VarCommitTime = "FLOWCI_GIT_COMMIT_TIME"
VarCommitNum = "FLOWCI_GIT_COMMIT_NUM"

CredentialName = client.GetVar('FLOWCI_GIT_CREDENTIAL', False)
KeyPath = None

ExitEvent = Event()
State = {}

class MyProgressPrinter(RemoteProgress):
    def update(self, op_code, cur_count, max_count=None, message=''):
        if max_count == '':
            max_count = 1

        percentage = "{00:.2f}%".format(cur_count / max_count * 100)
        print(op_code, cur_count, max_count, percentage, message or "")


def createDir(path):
    try:
        return os.makedirs(path)
    except FileExistsError:
        return path

def isHttpUrl(val):
    return val.startswith('http://') or val.startswith('https://')

def put(code, msg):
    global State
    State = {
        'code': code,
        'msg': msg
    }

def setupCredential(c):
    global GitUrl
    global KeyPath

    keyDir = createDir(os.path.join(domain.AgentJobDir, '.keys'))
    name = c['name']
    category = c['category']

    if isHttpUrl(GitUrl):
        if category != 'AUTH':
            put(1, '[ERROR] Credential type is miss match')
            ExitEvent.set()
            return
        

        index = GitUrl.index('://')
        index += 3

        username = urllib.parse.quote(c['pair']['username'])
        password = urllib.parse.quote(c['pair']['password'])
        GitUrl = "{}{}:{}@{}".format(
            GitUrl[:index], username, password, GitUrl[index:])

    else:
        if category != 'SSH_RSA':
            put(1, '[ERROR] Credential type is miss match')
            ExitEvent.set()
            return

        privateKey = c['pair']['privateKey']
        KeyPath = os.path.join(keyDir, name)
        print(privateKey, file=open(KeyPath, 'w'))
        os.chmod(KeyPath, 0o600)


def cleanUp():
    if KeyPath is not None:
        os.remove(KeyPath)


def gitPullOrClone():
    dest = os.path.join(domain.AgentJobDir, GitRepoName)
    api = client.Client()

    # load credential
    if CredentialName is not None:
        c = api.getCredential(CredentialName)
        setupCredential(c)

    # clean up
    if os.path.exists(dest):
        try:
            shutil.rmtree(dest)
        except OSError as e:
            print("[ERROR]: %s - %s." % (e.filename, e.strerror))

        # repo = Repo(dest)
        # repo.remote().pull(progress = MyProgressPrinter())

    # git clone
    env = {}
    if KeyPath is not None:
        env["GIT_SSH_COMMAND"] = 'ssh -o {} -o {} -i {}'.format(
            'UserKnownHostsFile=/dev/null', 'StrictHostKeyChecking=no', KeyPath)

    branchOrCommit = GitBranch
    if GitCommitId != None:
        branchOrCommit = GitCommitId

    if branchOrCommit == None:
        sys.exit("FLOWCI_GIT_BRANCH or FLOWCI_GIT_COMMIT_ID must be defined")

    try:
        repo = Repo.init(dest)
        repo.create_remote('origin', url=GitUrl)
        repo.remotes.origin.fetch(branchOrCommit, progress=MyProgressPrinter(), env=env)
        repo.git.checkout(branchOrCommit)

        head = repo.head
        if head != None and head.commit != None:
            sha = head.commit.hexsha
            message = head.commit.message
            dt = head.commit.committed_datetime
            email = head.commit.author.email

            api.addJobContext({
                VarAuthor: email,
                VarCommitID: sha,
                VarCommitMessage: message,
                VarCommitTime: dt.strftime('%Y-%m-%d %H:%M:%S'),
                VarCommitNum: 1
            })

        output = repo.git.submodule('update', '--init')
        print(output)

        put(0, '')
        ExitEvent.set()
    except Exception as e:
        put(1, 'Failed to clone git repo: ' + str(e))
        ExitEvent.set()

print("[INFO] -------- start git-clone plugin --------")

print("[INFO] url:        {}".format(GitUrl))
print("[INFO] repo name:  {}".format(GitRepoName))
print("[INFO] branch:     {}".format(GitBranch))
print("[INFO] commit:     {}".format(GitCommitId))
print("[INFO] timeout:    {}".format(GitTimeOut))
print("[INFO] credential: {}".format(CredentialName))

# start git clone process

p = Thread(target=gitPullOrClone)
p.start()

# kill if not finished within 60 seconds
val = ExitEvent.wait(timeout=GitTimeOut)
if val is False:
    cleanUp()
    sys.exit('[ERROR] git clone timeout')

if State['code'] is not 0:
    print(State['msg'])
    cleanUp()
    sys.exit("[INFO] -------- exit with error --------")

cleanUp()
print("[INFO] -------- done --------")
