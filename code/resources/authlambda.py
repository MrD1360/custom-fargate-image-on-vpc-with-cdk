def handler(event, context):
    # lambda that authenticates ftp client

    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'text/plain'
        },
        'body': 'Authenticated'
    }
