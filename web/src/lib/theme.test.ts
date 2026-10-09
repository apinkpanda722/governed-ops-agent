import {act,cleanup,renderHook} from "@testing-library/react";
import {afterEach,expect,test,vi} from "vitest";
import {readTheme,writeTheme,applyTheme} from "./theme";
import {useTheme} from "@/hooks/use-theme";
import {readFileSync} from "node:fs";
import path from "node:path";
afterEach(()=>{cleanup();vi.restoreAllMocks();vi.unstubAllGlobals();localStorage.clear();document.documentElement.classList.remove("dark");});
test("validates stored values and contains read/write failures",()=>{
 expect(writeTheme("other" as import("./theme").ThemeMode)).toBe(false);
 expect(readTheme()).toBe("system"); localStorage.setItem("govops-theme","wrong");expect(readTheme()).toBe("system");
 for(const mode of ["light","dark","system"] as const){expect(writeTheme(mode)).toBe(true);expect(readTheme()).toBe(mode);}
 vi.spyOn(Storage.prototype,"getItem").mockImplementation(()=>{throw Error();});expect(readTheme()).toBe("system");
 vi.spyOn(Storage.prototype,"setItem").mockImplementation(()=>{throw Error();});expect(writeTheme("dark")).toBe(false);
});
test("applies classes and follows system changes only in system mode",()=>{
 let dark=true;const listeners=new Set<()=>void>();
 const add=vi.fn((_name:string,fn:()=>void)=>listeners.add(fn)),remove=vi.fn((_name:string,fn:()=>void)=>listeners.delete(fn));
 vi.stubGlobal("matchMedia",()=>({get matches(){return dark;},addEventListener:add,removeEventListener:remove}));
 applyTheme("dark");expect(document.documentElement.classList.contains("dark")).toBe(true);
 applyTheme("light");expect(document.documentElement.classList.contains("dark")).toBe(false);
 const {result,unmount}=renderHook(()=>useTheme());expect(result.current.mode).toBe("system");expect(document.documentElement.classList.contains("dark")).toBe(true);
 dark=false;act(()=>listeners.forEach(fn=>fn()));expect(document.documentElement.classList.contains("dark")).toBe(false);
 act(()=>result.current.setMode("dark"));expect(readTheme()).toBe("dark");expect(document.documentElement.classList.contains("dark")).toBe(true);expect(listeners.size).toBe(0);
 act(()=>result.current.setMode("system"));expect(listeners.size).toBe(1);unmount();expect(listeners.size).toBe(0);expect(remove).toHaveBeenCalled();
});

test("mounted hook reads explicit choice and never subscribes to system changes",()=>{
 writeTheme("light");
 const add=vi.fn(), remove=vi.fn();
 vi.stubGlobal("matchMedia",()=>({matches:true,addEventListener:add,removeEventListener:remove}));
 const {result,unmount}=renderHook(()=>useTheme());
 expect(result.current.mode).toBe("light");expect(document.documentElement.classList.contains("dark")).toBe(false);expect(add).not.toHaveBeenCalled();
 unmount();expect(remove).not.toHaveBeenCalled();
});
test("css declares color-scheme so native scrollbars follow the theme",()=>{
 const css=readFileSync(path.resolve(process.cwd(),"src/app/globals.css"),"utf8");
 expect(css).toMatch(/:root\s*\{[^}]*color-scheme:\s*light/);
 expect(css).toMatch(/\.dark\s*\{[^}]*color-scheme:\s*dark/);
});
