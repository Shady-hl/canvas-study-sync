"""Ed tokens in the operating system's secure store; never in the archive."""
import argparse
import sys

SERVICE = 'CanvasStudySync.Ed'
REGIONS = ('eu', 'us', 'au')


class CredentialError(Exception):
    pass


def backend():
    """Choose only built-in OS stores, ignoring configured plaintext plugins."""
    try:
        if sys.platform == 'darwin':
            from keyring.backends.macOS import Keyring
        elif sys.platform == 'win32':
            from keyring.backends.Windows import WinVaultKeyring as Keyring
        else:
            from keyring.backends.SecretService import Keyring
        return Keyring()
    except Exception:
        raise CredentialError('Secure credential storage is unavailable. Paste a token for this run, or install the system credential service.') from None


def account(region):
    if region not in REGIONS:
        raise CredentialError('Choose the correct Ed region.')
    return region


def load(region):
    name = account(region)
    try:
        return backend().get_password(SERVICE, name) or ''
    except CredentialError:
        raise
    except Exception:
        raise CredentialError('The saved Ed token could not be read. Unlock the system credential store or paste a token for this run.') from None


def save(region, token):
    from ed_sync import normalize_token
    token = normalize_token(token)
    name = account(region)
    try:
        backend().set_password(SERVICE, name, token)
    except Exception:
        raise CredentialError('Ed was imported, but the token could not be saved. Unlock the system credential store and try again.') from None


def forget(region):
    name = account(region)
    try:
        store = backend()
        if store.get_password(SERVICE, name) is not None:
            store.delete_password(SERVICE, name)
    except Exception:
        raise CredentialError('The saved Ed token could not be removed. Unlock the system credential store and try again.') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--region', choices=REGIONS, default='eu')
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--forget', action='store_true')
    action.add_argument('--save', action='store_true', help='Validate a hidden token and remember it on this computer.')
    args = parser.parse_args(argv)
    try:
        if args.save:
            import getpass
            from ed_sync import EdClient, EdError
            token = getpass.getpass('Ed API token (hidden): ')
            client = EdClient(token, args.region)
            try:
                user = client.get('user')
                if not isinstance(user.get('user'), dict):
                    raise CredentialError('Ed did not confirm this account. The token was not saved.')
                save(args.region, token)
            finally:
                client.token = token = ''
            print('Ed token validated and saved in this computer’s secure credential store.')
        else:
            forget(args.region)
            print('Saved Ed token removed for this region. Other computers and Ed itself are unchanged.')
        return 0
    except CredentialError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        print('Ed sign-in could not be verified. No replacement token was saved.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
