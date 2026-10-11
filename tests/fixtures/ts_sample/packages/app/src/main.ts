import { UserService } from "@org/lib";
import { helper } from "./helper";

const svc = new UserService();
svc.setCurrent({ id: "1", name: helper() });
console.log(svc.greet());
