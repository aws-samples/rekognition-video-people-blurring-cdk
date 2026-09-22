FROM public.ecr.aws/lambda/python:3.14

COPY requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: \
    -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY video_processor.py app.py ${LAMBDA_TASK_ROOT}/

CMD ["app.lambda_function"]
