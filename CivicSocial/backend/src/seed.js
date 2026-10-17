import { hashPassword } from "./auth.js";
import { Follow, Post, User } from "./models.js";

const accounts = [
  ["mybmc", "MyBMC", true], ["aishamumbai", "Aisha Khan"], ["rohan_n", "Rohan Naik"], ["mumbailocal", "Mumbai Local"], ["nehasays", "Neha Shah"], ["arjunonroad", "Arjun Mehta"], ["bandrabuzz", "Bandra Buzz"], ["chemburconnect", "Chembur Connect"], ["powaiplan", "Powai Plan"], ["tanya_runs", "Tanya Rao"], ["cricketchai", "Cricket & Chai"], ["foodie_vikhroli", "Vikhroli Foodie"], ["sameerwrites", "Sameer Kulkarni"], ["sana_m", "Sana Merchant"], ["mumbairains", "Mumbai Rains"],
];
const posts = [
  ["aishamumbai", "The lane near our building has been full of potholes since the rain. Please fix it soon."], ["rohan_n", "Garbage has not been collected near the market for three days."], ["mumbailocal", "Water pressure was very low in our area this morning."], ["bandrabuzz", "Sunday sunset at Bandstand was unreal today."], ["cricketchai", "That last over! What a match."], ["tanya_runs", "Morning run done before the heat arrived."], ["chemburconnect", "Streetlight outside the bus stop has been off all week."], ["powaiplan", "Traffic near the lake was moving surprisingly well today."], ["nehasays", "Can somebody recommend a good bookshop in town?"], ["arjunonroad", "Overflowing bin near the crossing is attracting stray animals."], ["foodie_vikhroli", "Found an excellent misal place near the station."], ["mumbairains", "Drainage is blocked and the road floods after even a short shower."], ["sameerwrites", "The local train was on time today. A small miracle."], ["sana_m", "Weekend plans: coffee, friends, and zero alarms."], ["mybmc", "We are listening. Please share public-service updates through official channels."], ["aishamumbai", "@mybmc Is there an update on the damaged footpath near the school?"], ["rohan_n", "Mumbai football evenings are back!"], ["powaiplan", "Water leakage near the community garden is getting worse."],
];

export async function seed() {
  if (await User.count()) return;
  const passwordHash = await hashPassword("CivicSocialDemo123!");
  const users = {};
  for (const [handle, displayName, isOfficial = false] of accounts) {
    users[handle] = await User.create({ handle, displayName, email: `${handle}@seed.civicsocial.local`, emailVerified: true, avatarSeed: handle, passwordHash, isOfficial });
  }
  for (const [handle, text] of posts) await Post.create({ authorId: users[handle].id, text });
  const pairs = [["aishamumbai", "mybmc"], ["rohan_n", "mybmc"], ["chemburconnect", "mybmc"], ["powaiplan", "mumbailocal"], ["nehasays", "bandrabuzz"], ["tanya_runs", "cricketchai"]];
  for (const [follower, followee] of pairs) await Follow.create({ followerId: users[follower].id, followeeId: users[followee].id });
}
