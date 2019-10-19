import os
import time
import json
import mimetypes
import subprocess
import argparse
import shutil

import boto3
import pystache
import requests
from bs4 import BeautifulSoup
from botocore.exceptions import ClientError
from markdown import markdown
from mdx_gfm import GithubFlavoredMarkdownExtension

# Older releases aren't identified as proper 'releases' in github. The tarballs
# were provided by New Vector from their archives (and uploaded directly into
# S3 manually). Because the dump of tarball releases from New Vector doesn't 
# map precisely onto the meta persisted in github.com/vector-im/riot-web/releases
# (i.e. there is not an archived tarball for every tag in github), I have lazily
# recreated the relevant meta here manually.
OLDER_RELEASES = [
    {
        'name': '0.8.3',
        'body': '',
        'date': '2016-10-12'
    },
    {
        'name': '0.8.2-staging',
        'body': '',
        'date': '2016-10-05'
    },
    {
        'name': '0.8.1',
        'body': '',
        'date': '2016-09-21'
    },
    {
        'name': '0.8.0',
        'body': '',
        'date': '2016-09-21'
    },
    {
        'name': '0.7.5-r3',
        'body': '',
        'date': '2016-09-02'
    },
    {
        'name': '0.7.5-r2',
        'body': '',
        'date': '2016-09-01'
    },
    {
        'name': '0.7.5-r1',
        'body': '',
        'date': '2016-08-28'
    },
    {
        'name': '0.7.4',
        'body': '',
        'date': '2016-08-11'
    },
    {
        'name': '0.7.3',
        'body': '',
        'date': '2016-06-20'
    },
    {
        'name': '0.7.2',
        'body': '',
        'date': '2016-06-02'
    },
    {
        'name': '0.7.0',
        'body': '',
        'date': '2016-06-02'
    },
    {
        'name': '0.6.1',
        'body': '',
        'date': '2016-04-22'
    },
    {
        'name': '0.6.0',
        'body': '',
        'date': '2016-04-19'
    },
    {
        'name': '0.5.0',
        'body': '',
        'date': '2016-03-30'
    },
    {
        'name': '0.4.1',
        'body': '',
        'date': '2016-03-23'
    },
    {
        'name': '0.4.0',
        'body': '',
        'date': '2016-03-23'
    },
    {
        'name': '0.3.0',
        'body': '',
        'date': '2016-03-11'
    },
    {
        'name': '0.2.0',
        'body': '',
        'date': '2016-02-25'
    }
]

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

def is_version_uploaded(bucket, version):
    try:
        _ = bucket.Object(version + '/').last_modified
    except ClientError as e:
        if e.response['Error']['Code'] != '404':
            raise
        else:
            return False
    return True

def index(releases, bucket, older_releases=None):
    with open('site/index.mustache', 'r') as f:
        template = f.read()

    released = [
            {'name': release.get('name')[1:],
             'body': BeautifulSoup(markdown(release.get('body'), extensions=[GithubFlavoredMarkdownExtension()]), 'html.parser').prettify() if release.get('body') else '',
             'date': release.get('created_at')[:10]}
            for release in releases
            if is_version_uploaded(bucket, get_name(release))
            ]

    if older_releases is not None:
        released += older_releases

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

def upload_directory(name, root, bucket):
    for subdir, dirs, files in os.walk(root):
        for file in files:
            full_path = os.path.join(subdir, file)
            destination_path = name + full_path[len(root):]
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


def upload(releases, bucket, older_releases):
    for release in older_releases:
        name = release.get('name')
        if is_version_uploaded(bucket, name):
            print('%s: (older release) already hosted in bucket' % name)
        else:
            print('%s: (older release) uploading' % name)
            upload_directory(name, 'older_riots/%s' % name, bucket)
            print('%s: (older release) uploaded' % name)

    for release in releases:
        name = get_name(release)
        if name == '0.7.3':
            # We can't process any releases from 0.7.3 and older :( (yet)
            break
        if is_version_uploaded(bucket, name):
            print('%s: already hosted in bucket' % name)
        else:
            print('%s: not hosted in bucket; fetching...' % name)
            download_url = get_download_link(release)
            response = requests.get(download_url)
            os.makedirs('/tmp/downloads/%s' % name, exist_ok=True)
            if response.status_code == 200:
                filename = '/tmp/downloads/%s/%s.tar.gz' % (name, name)
                with open(filename, 'wb') as download:
                    for chunk in response.iter_content(chunk_size=128):
                        download.write(chunk)
            print('%s: tarball downloaded; exploding...' % name)
            os.makedirs('/tmp/exploded/%s' % name, exist_ok=True)
            subprocess.call(['tar', '-zxf', filename, '-C', '/tmp/exploded/%s/' % name])
            tar_root = '/tmp/exploded/%s/%s' % (name, os.listdir('/tmp/exploded/%s/' % name)[0])

            if name != '0.9.0':
                print('%s: tarball exploded; copying default config...' % name)
                shutil.copyfile(tar_root + '/config.sample.json', tar_root + '/config.json')
            else:
                print('%s: tarball exploded; patching in a config file for 0.9.0, released without config...' % name)
                shutil.copyfile('config.0.9.0.json', tar_root + '/config.json')

            print('%s: config inserted; uploading to s3...' % name)

            upload_directory(name, tar_root, bucket)
            print('%s upload complete; now available at https://riots.im/%s' % (name, name))


def invalidate_cloudfront_cache(cloudfrontClient, distributionId):
    paths = ['/', '/index.html']
    batch = {
        'Paths': {
            'Quantity': len(paths),
            'Items': paths
        },
        'CallerReference': str(time.time())
    }
    invalidation = cloudfrontClient.create_invalidation(
        DistributionId=distributionId,
        InvalidationBatch=batch,
    )
    return batch

def do_the_needful(aws_access_key_id, aws_secret_access_key, aws_bucket,
        aws_cloudfront_distribution_id, github_token, run_index, run_upload):

    session = boto3.Session(
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key
    )
    s3client = session.client('s3')
    s3resource = session.resource('s3')
    cloudfrontClient = session.client('cloudfront')

    releases = get_releases('vector-im', 'riot-web', token=github_token)
    bucket = s3resource.Bucket(aws_bucket)

    if run_index:
        index(releases, bucket, OLDER_RELEASES)
    if run_upload:
        upload(releases, bucket, OLDER_RELEASES)

    invalidate_cloudfront_cache(cloudfrontClient, aws_cloudfront_distribution_id)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Build a static site hosting historic Riot Web instances')
    parser.add_argument('--index', action="store_true")
    parser.add_argument('--upload', action="store_true")
    parser.add_argument('--aws-access-key-id', required=True)
    parser.add_argument('--aws-secret-access-key', required=True)
    parser.add_argument('--aws-bucket', required=True)
    parser.add_argument('--aws-cloudfront-distribution-id', required=True)
    parser.add_argument('--github-token', required=True)
    args = parser.parse_args()

    do_the_needful(
        aws_access_key_id=args.aws_access_key_id,
        aws_secret_access_key=args.aws_secret_access_key,
        aws_bucket=args.aws_bucket,
        aws_cloudfront_distribution_id=args.aws_cloudfront_distribution_id,
        github_token=args.github_token,
        run_index=args.index,
        run_upload=args.upload,
    )

def lambda_handler(who, cares):
    do_the_needful(
        aws_access_key_id=os.environ['aws_access_key_id'],
        aws_secret_access_key=os.environ['aws_secret_access_key'],
        aws_bucket=os.environ['aws_bucket'],
        aws_cloudfront_distribution_id=os.environ['aws_cloudfront_distribution_id'],
        github_token=os.environ['github_token'],
        run_index=True,
        run_upload=True
    )

    return {
        'statusCode': 200,
        'body': json.dumps('Hello from Lambda!')
    }

