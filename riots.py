import os
import sys
import mimetypes
import subprocess
import argparse
import shutil

import boto3
import pystache
import requests
from botocore.exceptions import ClientError
from markdown import markdown
from mdx_gfm import GithubFlavoredMarkdownExtension

parser = argparse.ArgumentParser(description='Build a static site hosting historic Riot Web instances')
parser.add_argument('--index')
parser.add_argument('--upload')
parser.add_argument('--aws-access-key-id', required=True)
parser.add_argument('--aws-secret-access-key', required=True)
parser.add_argument('--aws-bucket', required=True)
parser.add_argument('--github-token', required=True)
args = parser.parse_args()

session = boto3.Session(
    aws_access_key_id=args.aws_access_key_id,
    aws_secret_access_key=args.aws_secret_access_key
)
s3client = session.client('s3')
s3resource = session.resource('s3')

def compare_version_strings(a, b):
    a = [int(x) for x in a.split('.')]
    b = [int(x) for x in b.split('.')]

    for i in range(len(max(a,b))):
        a_part = a[i] if len(a) > i else 0
        b_part = b[i] if len(b) > i else 0
        if a_part != b_part:
            return (a_part > b_part) - (a_part < b_part)
    return 0

def get_releases(owner, repo, token):
    url = 'https://api.github.com/repos/%s/%s/releases' % (owner, repo)

    headers = {'Authorization': 'token %s' % token}
    response = requests.get(url, headers=headers)
    releases = response.json()

    while response.links.get('next'):
        response = requests.get(response.links.get('next').get('url'),
                                headers=headers)
        releases += response.json()

    return releases

def get_download_link(release, extension='.tar.gz'):
    tar_gzs = [asset for asset in release.get('assets')
               if asset.get('name').endswith(extension)]
    if len(tar_gzs) != 1:
        return None
    else:
        return tar_gzs[0].get('browser_download_url', None)

def get_name(release):
    return release.get('tag_name')[1:]

def is_version_uploaded(version):
    try:
        s3client.head_object(Bucket=args.aws_bucket, Key=version + '/')
    except ClientError as e:
        if e.response['Error']['Code'] != '404':
            raise
        else:
            return False
    return True

def index(releases, bucket):
    with open('site/index.mustache', 'r') as f:
        template = f.read()

    released = [
            {'name': release.get('name')[1:],
             'body': markdown(release.get('body'), extensions=[GithubFlavoredMarkdownExtension()]) if release.get('body') else '',
             'date': release.get('created_at')[:10]}
            for release in releases
            if is_version_uploaded(get_name(release))
            ]

    renderer = pystache.Renderer(missing_tags='strict')
    rendered = renderer.render(template, {'releases': released})

    bucket.put_object(Key='index.html', Body=rendered,
            ACL='public-read',
            ContentType='text/html')

    site_files = [('site/style.css', 'style.css'),
                  ('site/privacy.html', 'privacy.html')]
    for source, destination in site_files:
        with open(source, 'rb') as data:
            mime_type = mimetypes.guess_type(source)[0]
            if not mime_type:
                mime_type = 'application/octet-stream'
            bucket.put_object(Key=destination,
                              Body=data,
                              ACL='public-read',
                              ContentType=mime_type)


def upload(releases, bucket):
    for release in releases:
        name = get_name(release)
        if is_version_uploaded(name):
            print('%s: already hosted in bucket' % name, file=sys.stderr)
        else:
            print('%s: not hosted in bucket; fetching...' % name, file=sys.stderr)
            download_url = get_download_link(release)
            response = requests.get(download_url)
            os.makedirs('downloads/%s' % name, exist_ok=True)
            if response.status_code == 200:
                filename = 'downloads/%s/%s.tar.gz' % (name, name)
                with open(filename, 'wb') as download:
                    for chunk in response.iter_content(chunk_size=128):
                        download.write(chunk)
            print('%s: tarball downloaded; exploding...' % name, file=sys.stderr)
            os.makedirs('exploded/%s' % name, exist_ok=True)
            subprocess.call(['tar', '-zxf', filename, '-C', 'exploded/%s/' % name])
            tar_root = 'exploded/%s/%s' % (name, os.listdir('exploded/%s/' % name)[0])

            if name != '0.9.0':
                print('%s: tarball exploded; copying default config...' % name, file=sys.stderr)
                shutil.copyfile(tar_root + '/config.sample.json', tar_root + '/config.json')
            else:
                print('%s: tarball exploded; patching in a config file for 0.9.0, released without config...' % name, file=sys.stderr)
                shutil.copyfile('config.0.9.0.json', tar_root + '/config.json')

            print('%s: config inserted; uploading to s3...' % name, file=sys.stderr)

            for subdir, dirs, files in os.walk(tar_root):
                for file in files:
                    full_path = os.path.join(subdir, file)
                    destination_path = name + full_path[len(tar_root):]
                    with open(full_path, 'rb') as data:
                        mime_type = mimetypes.guess_type(full_path)[0]
                        if not mime_type:
                            mime_type = 'application/octet-stream'
                        print('Putting %s (%s)' % (destination_path, mime_type))
                        bucket.put_object(Key=destination_path,
                                          Body=data,
                                          ACL='public-read',
                                          ContentType=mime_type)
            # If we don't do this, we can't easily test whether the 'directory' exists in S3
            bucket.put_object(Key='%s/' % name, Body='')
            print('%s upload complete; now available at https://riots.im/%s' % (name, name))

releases = get_releases('vector-im', 'riot-web', token=args.github_token)
bucket = s3resource.Bucket(args.aws_bucket)

if args.index:
    index(releases, bucket)
if args.upload:
    upload(release, bucket)
