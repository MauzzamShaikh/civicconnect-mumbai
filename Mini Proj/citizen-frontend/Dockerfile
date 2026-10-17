FROM node:22-alpine
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm install --no-audit --no-fund
COPY . .
EXPOSE 5174
CMD ["npm", "run", "dev"]
